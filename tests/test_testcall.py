import subprocess
from pathlib import Path

from aivoicemail import cli, testcall
from aivoicemail.calllog import CallLog
from aivoicemail.tts import render_all
from aivoicemail.tts.placeholder import PlaceholderEngine

ID = "0f8fad5b-d9cb-469f-a165-70867728950e"


def rendered(cfg):
    render_all(cfg, {}, cfg.paths.generated_dir, engine=PlaceholderEngine(), log=lambda *a: None)
    return cfg


class Clock:
    def __init__(self, t=1_790_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def worker_runner(cfg, outcome="message", seen=None):
    """Stands in for SIPp + worker: records the command and writes the call-log entry the worker would."""
    def runner(cmd, capture_output, text):
        if seen is not None:
            seen.append(cmd)
            seen.append(Path(cmd[cmd.index("-sf") + 1]).read_text())
        line = cfg.line_by_did(cmd[cmd.index("-s") + 1])
        CallLog(cfg.paths.data_dir / "calllog", 90).record(
            {"id": ID, "line": line.id, "did": line.did, "caller": "+15550100001", "lang_choice": "nl"},
            outcome, message_id="<m@acme.example>")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return runner


def test_plan_call_modes(cfg):
    d = {"be-menu": 6.0, "be-notice-nl": 24.0, "be-notice-auto": 28.0, "pl-notice-pl": 26.0}
    be, pl = cfg.lines
    assert testcall.plan_call(be, "1", d) == ("inband", 44000)
    assert testcall.plan_call(be, None, d) == ("plain", 60000)
    assert testcall.plan_call(pl, None, d) == ("plain", 47000)
    assert testcall.speech_lead(be, "1", d) == 25.0


def test_menu_call_uses_inband_digit_and_waits_for_outcome(cfg):
    rendered(cfg)
    seen, out, clock = [], [], Clock()
    rc = testcall.run(cfg, runner=worker_runner(cfg, seen=seen), clock=clock, sleep=clock.sleep, out=out.append)
    assert rc == 0
    cmd, scenario = seen
    assert cmd[:2] == ["sipp", "127.0.0.1:5060"] and cmd[cmd.index("-s") + 1] == "3220000001" and "-inf" not in cmd
    assert "inband_1.pcap" in scenario and "[field0]" not in scenario and "telephone-event" not in scenario
    assert out[-1] == f"test-call: outcome=message message_id=<m@acme.example> item={ID}"


def test_menu_call_digit_2_substitutes_correct_pcap(cfg):
    rendered(cfg)
    seen, clock = [], Clock()
    rc = testcall.run(cfg, digit="2", runner=worker_runner(cfg, seen=seen), clock=clock, sleep=clock.sleep,
                      out=lambda *a: None)
    assert rc == 0
    cmd, scenario = seen
    assert "-inf" not in cmd
    assert "inband_2.pcap" in scenario and "[field0]" not in scenario


def test_single_language_line_has_no_digit(cfg):
    rendered(cfg)
    seen, clock = [], Clock()
    assert testcall.run(cfg, line_id="pl", runner=worker_runner(cfg, seen=seen), clock=clock, sleep=clock.sleep,
                        out=lambda *a: None) == 0
    assert "-inf" not in seen[0] and seen[0][seen[0].index("-s") + 1] == "48320000001"


def test_no_outcome_times_out(cfg):
    rendered(cfg)
    clock, out = Clock(), []
    runner = lambda cmd, capture_output, text: subprocess.CompletedProcess(cmd, 0, "", "")
    assert testcall.run(cfg, runner=runner, timeout=30, clock=clock, sleep=clock.sleep, out=out.append) == 1
    assert "no delivered call" in out[-1]


def test_send_failed_is_not_delivered(cfg):
    rendered(cfg)
    clock = Clock()
    rc = testcall.run(cfg, runner=worker_runner(cfg, outcome="send-failed"), timeout=10, clock=clock,
                      sleep=clock.sleep, out=lambda *a: None)
    assert rc == 1


def test_sipp_failure(cfg):
    rendered(cfg)
    out = []
    runner = lambda cmd, capture_output, text: subprocess.CompletedProcess(cmd, 1, "", "401 Unauthorized")
    assert testcall.run(cfg, runner=runner, out=out.append) == 1 and "401 Unauthorized" in out[-1]


def test_invalid_digit_and_line(cfg):
    rendered(cfg)
    assert testcall.run(cfg, digit="7", out=lambda *a: None) == 2
    assert testcall.run(cfg, line_id="pl", digit="1", out=lambda *a: None) == 2
    assert testcall.run(cfg, line_id="zz", out=lambda *a: None) == 2


def test_missing_prompts(cfg):
    assert testcall.run(cfg, out=lambda *a: None) == 2


def test_fake_providers_requires_outbox_email(cfg):
    rendered(cfg)
    clock = Clock()
    run = lambda: testcall.run(cfg, fake_providers=True, runner=worker_runner(cfg), clock=clock, sleep=clock.sleep,
                               out=lambda *a: None)
    assert run() == 1
    outbox = cfg.paths.data_dir / "outbox"
    outbox.mkdir(parents=True)
    (outbox / "1-1.eml").write_text(f"Subject: x\n\nID: {ID}\n")
    assert run() == 0


def test_cli_registers_test_call():
    args = cli.build_parser().parse_args(["test-call", "--line", "pl", "--timeout", "10"])
    assert args.func is cli.cmd_test_call and args.line == "pl" and args.timeout == 10
