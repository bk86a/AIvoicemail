"""Fake providers for CI and first runs: fixed transcript and summary; nothing leaves the host."""
import wave
from pathlib import Path

from ..llm.schema import apply_language_override

FAKE_TRANSCRIPT = "This is a test message recorded in aivoicemail fake-provider mode. Please call back."
FAKE_SUMMARY = {
    "caller_name": None, "company": None, "subject": "Test call (fake providers)", "callback_number": None,
    "language": "en", "urgency": "low",
    "summary": "Fake-provider test message; no speech recognition or language model was used.",
    "requested_action": None,
}


class FakeSpeech:
    def speech_seconds(self, wav) -> float:
        """Recording length (every second counts as speech); robust to a header Asterisk never finalised."""
        by_size = max(Path(wav).stat().st_size - 44, 0) / 16000
        try:
            with wave.open(str(wav), "rb") as w:
                return max(w.getnframes() / w.getframerate(), by_size)
        except (wave.Error, EOFError):
            return by_size

    def transcribe(self, wav, lang, candidates) -> str:
        return FAKE_TRANSCRIPT


def fake_transcribe(wav, lang, candidates):
    return FAKE_TRANSCRIPT, "fake"


def fake_summarise(transcript, meta, email_language):
    return apply_language_override(dict(FAKE_SUMMARY), meta), "fake"
