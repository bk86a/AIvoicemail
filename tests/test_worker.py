import json

from aivoicemail import worker
from aivoicemail.fake.smtp_capture import messages
from aivoicemail.processor import Processor
from aivoicemail.spool import Item, SpoolError
from conftest import write_wav

ID1 = "0f8fad5b-d9cb-469f-a165-70867728950e"
ID2 = "11111111-2222-4333-8444-555555555555"


class ListSpool:
    def __init__(self, items, error=None, orphans=0):
        self.items, self.error, self.orphan_count, self.orphan_calls = items, error, orphans, 0

    def orphans(self):
        self.orphan_calls += 1
        if isinstance(self.orphan_count, Exception):
            raise self.orphan_count
        return self.orphan_count

    def list(self):
        if self.error:
            raise self.error
        return list(self.items)


class Recorder:
    def __init__(self):
        self.checks, self.prunes = [], 0

    def check(self, waiting, *, last_list_ok, now, spool_label, orphans=0):
        self.checks.append((list(waiting), last_list_ok, now, spool_label))
        self.orphans = orphans

    def prune(self):
        self.prunes += 1


class Deps:
    def __init__(self, cfg, spool, times):
        self.cfg, self.spool, self.logs = cfg, spool, []
        self.alerter = self.calllog = Recorder()
        self._times = iter(times)

    def clock(self):
        return next(self._times)

    def log(self, msg):
        self.logs.append(msg)


class FakeProcessor:
    def __init__(self, results):
        self.results, self.seen, self.evicted = results, [], []

    def evict(self, keep_ids):
        self.evicted.append(set(keep_ids))

    def process(self, item):
        self.seen.append(item.id)
        result = self.results[item.id]
        if isinstance(result, Exception):
            raise result
        return result


def test_run_continues_after_item_exception(cfg):
    d = Deps(cfg, ListSpool([Item(ID1, 0, True), Item(ID2, 0, True)]), [100, 5000, 5001])
    p = FakeProcessor({ID1: RuntimeError("boom"), ID2: "acked"})
    worker.run(d, p, once=True)
    assert p.seen == [ID1, ID2]
    waiting, last_ok, now, label = d.alerter.checks[0]
    assert [i.id for i in waiting] == [ID1] and last_ok == 5000 and now == 5001
    assert label == "directory missing or unreadable"
    assert any(ID1 in l and "RuntimeError" in l for l in d.logs)
    assert d.calllog.prunes == 1


def test_list_failure_keeps_last_ok(cfg):
    d = Deps(cfg, ListSpool([], error=SpoolError("gone")), [100, 200])
    worker.run(d, FakeProcessor({}), once=True)
    assert d.alerter.checks[0][1] == 100 and any("list failed" in l for l in d.logs)


def test_retry_items_are_waiting_acked_are_not(cfg):
    d = Deps(cfg, ListSpool([Item(ID1, 0, True), Item(ID2, 0, True)]), [1, 2, 3])
    worker.run(d, FakeProcessor({ID1: "retry", ID2: "acked"}), once=True)
    assert [i.id for i in d.alerter.checks[0][0]] == [ID1]


def test_state_evicted_only_after_a_successful_list(cfg):
    d = Deps(cfg, ListSpool([Item(ID1, 0, True)]), [1, 2, 3])
    p = FakeProcessor({ID1: "retry"})
    worker.run(d, p, once=True)
    assert p.evicted == [{ID1}]
    d = Deps(cfg, ListSpool([], error=SpoolError("gone")), [1, 2])
    p = FakeProcessor({})
    worker.run(d, p, once=True)
    assert p.evicted == []


def test_orphans_passed_to_the_alerter_and_scan_failures_logged(cfg):
    d = Deps(cfg, ListSpool([], orphans=3), [1, 2, 3])
    worker.run(d, FakeProcessor({}), once=True)
    assert d.alerter.orphans == 3
    d = Deps(cfg, ListSpool([], orphans=SpoolError("orphans: exit 2")), [1, 2, 3])
    worker.run(d, FakeProcessor({}), once=True)
    assert d.alerter.orphans == 0 and any("orphan check failed" in l for l in d.logs)
    d = Deps(cfg, ListSpool([], error=SpoolError("gone"), orphans=3), [1, 2, 3])
    worker.run(d, FakeProcessor({}), once=True)
    assert d.spool.orphan_calls == 0


def test_loop_sleeps_poll_seconds(cfg):
    d = Deps(cfg, ListSpool([]), [1, 2, 3, 4])
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        raise KeyboardInterrupt

    try:
        worker.run(d, FakeProcessor({}), sleep=sleep)
    except KeyboardInterrupt:
        pass
    assert sleeps == [30]


def test_fake_mode_end_to_end(cfg):
    ready = cfg.spool.path / "ready"
    write_wav(ready / f"{ID1}.wav", 5, tone_hz=440)
    (ready / f"{ID1}.json").write_text(json.dumps({
        "id": ID1, "line": "be", "did": "3220000001", "caller": "+32470123456", "lang_choice": "fr",
        "started_at": "2026-09-22T10:00:00Z", "ended_at": "2026-09-22T10:00:05Z", "duration_s": 5, "has_audio": True}))
    logs = []
    deps, closers = worker.build(cfg, {}, fake=True, log=logs.append)
    try:
        worker.run(deps, Processor(deps), once=True)
    finally:
        for close in closers:
            close()
    [msg] = messages(cfg.paths.data_dir / "outbox")
    assert msg["To"] == "info@acme.example" and msg["Subject"].startswith("[Voicemail] low - +32470123456")
    assert "Transcript (fr):" in msg.get_content()
    assert list(ready.iterdir()) == []
    assert any("fake providers mode" in l for l in logs)
