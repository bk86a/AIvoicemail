"""Local faster-whisper engine: speech detection (bundled Silero VAD) and transcription."""
from pathlib import Path

SAMPLE_RATE = 16000


class WhisperLocal:
    def __init__(self, model: str, threads: int, download_root: Path):
        self._name, self._threads, self._root = model, threads, Path(download_root)
        self._model = None

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(self._name, device="cpu", compute_type="int8",
                                       cpu_threads=self._threads, download_root=str(self._root))
        return self._model

    def speech_seconds(self, wav) -> float:
        from faster_whisper import decode_audio
        from faster_whisper.vad import get_speech_timestamps
        audio = decode_audio(str(wav), sampling_rate=SAMPLE_RATE)
        return sum(s["end"] - s["start"] for s in get_speech_timestamps(audio)) / SAMPLE_RATE

    def transcribe(self, wav, lang, candidates) -> str:
        from faster_whisper import decode_audio
        model = self._load()
        audio = decode_audio(str(wav), sampling_rate=SAMPLE_RATE)
        language = lang
        if lang == "auto":
            _, _, probs = model.detect_language(audio)
            allowed = [p for p in probs if p[0] in candidates] or list(probs)
            language = max(allowed, key=lambda p: p[1])[0]
        segments, _ = model.transcribe(audio, language=language, beam_size=5, vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()
