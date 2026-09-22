#!/usr/bin/env python3
"""Assert the spool's ready/ contents after a SIPp scenario, then empty ready/ (unless --keep).

Usage: check_spool.py <spool_dir> <expect_json_count> <expect_wav_count>
                      [--line ID] [--lang CODE|auto] [--did DID] [--caller NUM] [--keep]
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

KEYS = {"id", "line", "did", "caller", "lang_choice", "started_at", "ended_at", "duration_s", "has_audio"}
ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def fail(msg: str) -> None:
    print(f"check_spool FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("spool", type=Path)
    ap.add_argument("n_json", type=int)
    ap.add_argument("n_wav", type=int)
    ap.add_argument("--line")
    ap.add_argument("--lang")
    ap.add_argument("--did")
    ap.add_argument("--caller")
    ap.add_argument("--keep", action="store_true", help="do not empty ready/ afterwards")
    a = ap.parse_args()
    ready = a.spool / "ready"

    # The hangup handler runs just after the BYE; allow it a moment to finish.
    deadline = time.monotonic() + 5
    while True:
        jsons = sorted(ready.glob("*.json"))
        if len(jsons) >= a.n_json or time.monotonic() > deadline:
            break
        time.sleep(0.2)
    if a.n_json == 0:
        time.sleep(2)
        jsons = sorted(ready.glob("*.json"))
    wavs = sorted(ready.glob("*.wav"))
    leftovers = sorted(p.name for p in (a.spool / "tmp").glob("*"))

    if len(jsons) != a.n_json:
        fail(f"expected {a.n_json} json, found {[p.name for p in jsons]}")
    if len(wavs) != a.n_wav:
        fail(f"expected {a.n_wav} wav, found {[p.name for p in wavs]}")
    if leftovers:
        fail(f"tmp/ not empty: {leftovers}")

    for path in jsons:
        meta = json.loads(path.read_text())
        print(f"check_spool: {path.name} {json.dumps(meta)}")
        if set(meta) != KEYS:
            fail(f"keys {sorted(meta)} != {sorted(KEYS)}")
        if meta["id"] != path.stem or not ID_RE.fullmatch(meta["id"]):
            fail(f"bad id {meta['id']!r} for {path.name}")
        for key, want in (("line", a.line), ("lang_choice", a.lang), ("did", a.did), ("caller", a.caller)):
            if want is not None and meta[key] != want:
                fail(f"{key} {meta[key]!r} != {want!r}")
        for k in ("started_at", "ended_at"):
            if not TS_RE.fullmatch(meta[k]):
                fail(f"{k} {meta[k]!r} not ISO UTC")
        start = datetime.fromisoformat(meta["started_at"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(meta["ended_at"].replace("Z", "+00:00"))
        if end < start:
            fail("ended_at before started_at")
        wav = ready / f"{meta['id']}.wav"
        if meta["has_audio"] is not wav.exists():
            fail(f"has_audio {meta['has_audio']} but wav exists={wav.exists()}")
        if meta["has_audio"]:
            if not isinstance(meta["duration_s"], int) or meta["duration_s"] < 5:
                fail(f"duration_s {meta['duration_s']!r} too short for a recorded call")
            if wav.read_bytes()[:4] != b"RIFF":
                fail("wav has no RIFF header")
        elif meta["duration_s"] != 0:
            fail(f"duration_s {meta['duration_s']} without audio")

    if not a.keep:
        for p in jsons + wavs:
            p.unlink()
    print(f"check_spool OK: {a.n_json} json, {a.n_wav} wav")


if __name__ == "__main__":
    main()
