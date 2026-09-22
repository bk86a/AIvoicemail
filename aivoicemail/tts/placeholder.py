"""Deterministic stand-in voice for CI and first runs: no network, no model.

Per segment: a 150 ms beep, silence sized from the text (60 ms per character), then the pause."""
from .audio import silence, tone, wav_bytes

MS_PER_CHAR = 60


class PlaceholderEngine:
    def render(self, prompt) -> bytes:
        pcm = bytearray()
        for seg in prompt.segments:
            pcm += tone(150, 660)
            pcm += silence(len(seg.text) * MS_PER_CHAR)
            pcm += silence(400 if seg.break_ms is None else seg.break_ms)
        return wav_bytes(bytes(pcm))
