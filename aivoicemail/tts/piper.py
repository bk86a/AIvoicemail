"""Piper TTS (local, default): voices downloaded once into <data_dir>/voices, output resampled to 8 kHz.

Pronunciation overrides are passed to Piper as raw phonemes ([[ ... ]])."""
import re
import subprocess
import sys
from pathlib import Path

from .audio import RATE, resample, silence, wav_bytes


class PiperEngine:
    def __init__(self, voices, voices_dir, pronunciation=None, *, synthesize=None, download=None):
        self.voices, self.voices_dir = dict(voices), Path(voices_dir)
        self.pronunciation = pronunciation or {}
        self._synthesize = synthesize or self._piper
        self._download = download or self._download_voice
        self._loaded = {}

    def text_for(self, seg) -> str:
        text = seg.text
        for word, per_lang in self.pronunciation.items():
            ipa = per_lang.get(seg.lang)
            if ipa:
                text = re.sub(rf"\b{re.escape(word)}\b", lambda m: f"[[ {ipa} ]]", text)
        return text

    def voice_path(self, name) -> Path:
        path = self.voices_dir / f"{name}.onnx"
        if not path.is_file():
            self._download(name)
        return path

    def _download_voice(self, name) -> None:
        self.voices_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-m", "piper.download_voices", "--download-dir", str(self.voices_dir), name],
                       check=True)

    def _piper(self, voice_name, text):
        from piper import PiperVoice
        voice = self._loaded.get(voice_name)
        if voice is None:
            voice = self._loaded[voice_name] = PiperVoice.load(str(self.voice_path(voice_name)))
        return [(chunk.sample_rate, chunk.audio_int16_bytes) for chunk in voice.synthesize(text)]

    def render(self, prompt) -> bytes:
        pcm = bytearray()
        for seg in prompt.segments:
            for i, (rate, data) in enumerate(self._synthesize(self.voices[seg.lang], self.text_for(seg))):
                if i and prompt.sentence_ms:
                    pcm += silence(prompt.sentence_ms)
                pcm += data if rate == RATE else resample(data, rate)
            pcm += silence(400 if seg.break_ms is None else seg.break_ms)
        return wav_bytes(bytes(pcm))
