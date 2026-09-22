"""Shared STT helpers and the local speech engine interface."""
from pathlib import Path
from typing import Protocol, Sequence


def nonempty(text) -> str:
    if not text or not str(text).strip():
        raise RuntimeError("empty transcript")
    return str(text).strip()


class SpeechEngine(Protocol):
    def speech_seconds(self, wav: Path) -> float: ...

    def transcribe(self, wav: Path, lang: str, candidates: Sequence[str]) -> str: ...
