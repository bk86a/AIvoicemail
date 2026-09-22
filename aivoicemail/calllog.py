"""Call log: one JSON line of metadata per call event, one file per UTC day, deleted after retention.

Never transcript, summary or audio."""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUTCOMES = ("message", "missed", "fallback-speech-detection", "fallback-transcription", "fallback-summary",
            "fallback-failed-repeatedly", "send-failed", "acked-after-retry")
_META_KEYS = (("id", "id"), ("line", "line"), ("did", "did"), ("caller", "caller"), ("language", "lang_choice"),
              ("started_at", "started_at"), ("duration_s", "duration_s"), ("has_audio", "has_audio"))
FILE_RE = re.compile(r"calls-(\d{4}-\d{2}-\d{2})\.jsonl")


def _utc(now):
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc)


class CallLog:
    def __init__(self, directory, retention_days, *, log=None):
        self.directory, self.retention_days, self.log = Path(directory), retention_days, log

    def record(self, meta, outcome, *, providers=None, message_id=None, now=None) -> None:
        providers = providers or {}
        now = _utc(now)
        entry = {"logged_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
        entry.update({key: meta.get(src) for key, src in _META_KEYS})
        entry.update(outcome=outcome, transcribed_by=providers.get("transcript"),
                     summarised_by=providers.get("summary"), message_id=message_id)
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o750)
            with open(self.directory / f"calls-{now:%Y-%m-%d}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as e:
            if self.log:
                self.log(f"{meta.get('id')}: call log write failed: {type(e).__name__}")

    def prune(self, now=None) -> int:
        cutoff = _utc(now).date() - timedelta(days=self.retention_days)
        try:
            files = list(self.directory.iterdir())
        except OSError:
            return 0
        removed = 0
        for path in files:
            m = FILE_RE.fullmatch(path.name)
            if m and datetime.strptime(m.group(1), "%Y-%m-%d").date() < cutoff:
                try:
                    path.unlink()
                    removed += 1
                except OSError as e:
                    if self.log:
                        self.log(f"call log prune failed for {path.name}: {type(e).__name__}")
        return removed


def read_entries(directory) -> list[dict]:
    out = []
    for path in sorted(Path(directory).glob("calls-*.jsonl")):
        out += [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    return out
