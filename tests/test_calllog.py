import json
from datetime import datetime, timezone

from aivoicemail.calllog import CallLog, read_entries

META = {"id": "0f8fad5b-d9cb-469f-a165-70867728950e", "line": "be", "did": "3220000001",
        "caller": "+32470123456", "lang_choice": "nl", "started_at": "2026-09-13T09:00:00Z",
        "ended_at": "2026-09-13T09:00:30Z", "duration_s": 30, "has_audio": True,
        "transcript": "SECRET TRANSCRIPT", "summary": "SECRET SUMMARY"}
NOW = datetime(2026, 9, 21, 10, 0, 0, tzinfo=timezone.utc)
KEYS = {"logged_at", "id", "line", "did", "caller", "language", "started_at", "duration_s",
        "has_audio", "outcome", "transcribed_by", "summarised_by", "message_id"}


def test_record_appends_json_lines_to_daily_file(tmp_path):
    log = CallLog(tmp_path / "calllog", 90)
    log.record(META, "message", providers={"transcript": "whisper_local", "summary": "primary"}, message_id="<m1>", now=NOW)
    log.record(META, "send-failed", now=NOW)
    raw = (tmp_path / "calllog" / "calls-2026-09-21.jsonl").read_text(encoding="utf-8")
    assert raw.endswith("\n") and raw.count("\n") == 2
    first, second = (json.loads(l) for l in raw.splitlines())
    assert set(first) == KEYS
    assert first["logged_at"] == "2026-09-21T10:00:00Z" and first["language"] == "nl"
    assert first["outcome"] == "message" and first["message_id"] == "<m1>"
    assert first["transcribed_by"] == "whisper_local" and first["summarised_by"] == "primary"
    assert second["outcome"] == "send-failed" and second["message_id"] is None and second["transcribed_by"] is None


def test_record_never_contains_content(tmp_path):
    CallLog(tmp_path, 90).record(META, "message", now=NOW)
    text = (tmp_path / "calls-2026-09-21.jsonl").read_text(encoding="utf-8")
    assert "SECRET" not in text and "ended_at" not in text


def test_record_utf8_not_escaped(tmp_path):
    CallLog(tmp_path, 90).record({"id": "x", "caller": "Łódź"}, "missed", now=NOW)
    assert "Łódź" in (tmp_path / "calls-2026-09-21.jsonl").read_text(encoding="utf-8")


def test_default_now_is_utc(tmp_path):
    CallLog(tmp_path, 90).record(META, "missed")
    [entry] = read_entries(tmp_path)
    assert entry["logged_at"].endswith("Z")


def test_write_failure_is_logged_without_caller_number(tmp_path):
    (tmp_path / "calllog").write_text("a file, not a directory")
    logs = []
    CallLog(tmp_path / "calllog", 90, log=logs.append).record(META, "missed", now=NOW)
    assert len(logs) == 1 and "call log" in logs[0] and "+32470123456" not in logs[0]


def test_prune_deletes_files_older_than_retention(tmp_path):
    for day in ("2026-06-22", "2026-06-23", "2026-09-21"):
        (tmp_path / f"calls-{day}.jsonl").write_text("{}\n")
    (tmp_path / "unrelated.txt").write_text("keep")
    assert CallLog(tmp_path, 90).prune(now=NOW) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["calls-2026-06-23.jsonl", "calls-2026-09-21.jsonl", "unrelated.txt"]


def test_prune_missing_directory_is_noop(tmp_path):
    assert CallLog(tmp_path / "none", 90).prune(now=NOW) == 0


def test_read_entries_orders_by_day(tmp_path):
    log = CallLog(tmp_path, 90)
    log.record(dict(META, id="b"), "missed", now=NOW)
    log.record(dict(META, id="a"), "missed", now=datetime(2026, 9, 20, tzinfo=timezone.utc))
    assert [e["id"] for e in read_entries(tmp_path)] == ["a", "b"]
