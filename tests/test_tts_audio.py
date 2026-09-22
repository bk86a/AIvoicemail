import io
import wave

import pytest

from aivoicemail.tts import audio
from conftest import write_wav


def test_silence_and_tone_lengths():
    assert audio.silence(250) == b"\0\0" * 2000
    assert len(audio.tone(100, 1000)) == 1600


def test_wav_bytes_format():
    with wave.open(io.BytesIO(audio.wav_bytes(audio.silence(1000)))) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 8000, 8000)


def test_check_format_and_duration(tmp_path):
    good = write_wav(tmp_path / "good.wav", 2)
    assert audio.check_format(good) is None and audio.duration(good) == 2.0
    assert "8 kHz mono 16-bit" in audio.check_format(write_wav(tmp_path / "hi.wav", 1, rate=16000))
    (tmp_path / "junk.wav").write_bytes(b"nope")
    assert "not a readable WAV" in audio.check_format(tmp_path / "junk.wav")


def test_resample_16k_to_8k():
    pytest.importorskip("av")
    pcm16k = b"\x10\x00" * 16000  # 1 s at 16 kHz
    out = audio.resample(pcm16k, 16000)
    assert abs(len(out) // 2 - 8000) <= 100
