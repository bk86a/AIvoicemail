"""vm-finalize against a temporary spool (VM_SPOOL override), as the Asterisk hangup handler calls it."""
import json
import os
import subprocess

import pytest

from conftest import ROOT

SCRIPT = ROOT / "asterisk" / "bin" / "vm-finalize"
UID = "1790012257.3"


def run(spool, caller="15550100001", recorded="1", wav=None, line="be", lang="nl", did="3220000001"):
    (spool / "tmp").mkdir(parents=True, exist_ok=True)
    if wav is not None:
        (spool / "tmp" / f"{UID}.wav").write_bytes(wav)
    r = subprocess.run(["sh", str(SCRIPT), line, lang, caller, did, "1790012257", UID, recorded],
                       env={**os.environ, "VM_SPOOL": str(spool)}, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    ready = spool / "ready"
    metas = [json.loads(p.read_text()) for p in sorted(ready.glob("*.json"))] if ready.exists() else []
    wavs = sorted(p.name for p in ready.glob("*.wav")) if ready.exists() else []
    return metas, wavs, sorted(p.name for p in (spool / "tmp").iterdir())


def wav_bytes(seconds):
    return b"RIFF" + b"\0" * 40 + b"\1" * (16000 * seconds)


@pytest.mark.parametrize("raw,expected", [
    ("15550100001", "+15550100001"),          # carrier delivers E.164 without '+'
    ("+15550100001", "+15550100001"),
    ("48123456789", "+48123456789"),
    ("12345678", "+12345678"),                # 8 digits
    ("123456789012345", "+123456789012345"),  # 15 digits
    ("1234567", "1234567"),                   # too short for E.164
    ("1234567890123456", "1234567890123456"),  # too long
    ("0470111222", "0470111222"),             # national format, leading 0
    ("", "withheld"),
    ("anonymous", "withheld"),
])
def test_caller_normalisation(tmp_path, raw, expected):
    metas, _, _ = run(tmp_path, caller=raw, wav=wav_bytes(6))
    assert metas[0]["caller"] == expected


def test_recorded_call_moves_wav(tmp_path):
    [meta], wavs, tmp = run(tmp_path, wav=wav_bytes(6))
    assert meta["has_audio"] is True and meta["duration_s"] == 6
    assert wavs == [f"{meta['id']}.wav"] and tmp == []
    assert meta["line"] == "be" and meta["lang_choice"] == "nl" and meta["did"] == "3220000001"
    assert meta["started_at"] == "2026-09-21T17:37:37Z"


def test_files_are_group_readable(tmp_path):
    [meta], _, _ = run(tmp_path, wav=wav_bytes(6))
    mode = (tmp_path / "ready" / f"{meta['id']}.json").stat().st_mode
    assert mode & 0o040


def test_not_recorded_writes_json_only_and_removes_wav(tmp_path):
    [meta], wavs, tmp = run(tmp_path, recorded="0", wav=wav_bytes(6))
    assert meta["has_audio"] is False and meta["duration_s"] == 0 and wavs == [] and tmp == []


def test_header_only_wav_is_no_audio(tmp_path):
    [meta], wavs, tmp = run(tmp_path, wav=b"RIFF" + b"\0" * 40)
    assert meta["has_audio"] is False and meta["duration_s"] == 0 and wavs == [] and tmp == []


@pytest.mark.parametrize("line,lang,ok", [
    ("be", "de", True), ("main2", "auto", True), ("a" * 16, "en", True),
    ("BE", "nl", False), ("a;b", "nl", False), ("", "nl", False), ("a" * 17, "nl", False),
    ("be", "d", False), ("be", "NL", False), ("be", "nl;", False), ("be", "", False),
])
def test_line_and_language_validation(tmp_path, line, lang, ok):
    metas, wavs, _ = run(tmp_path, line=line, lang=lang, wav=wav_bytes(6))
    assert (len(metas) == 1) is ok


def test_injection_in_caller_and_did_is_reduced_to_digits(tmp_path):
    [meta], _, _ = run(tmp_path, caller="15550100001';touch /tmp/x;'`id`$(id)", did="3220000001$(id)")
    assert meta["caller"] == "+15550100001" and meta["did"] == "3220000001"
