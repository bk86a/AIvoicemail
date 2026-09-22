import dataclasses
import math
import struct
import wave
from pathlib import Path

import pytest

from aivoicemail import config

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "config" / "aivoicemail.example.toml"
ENV = {"STT_API_KEY": "stt-key", "LLM_API_KEY": "llm-key", "SMTP_USER": "user", "SMTP_PASSWORD": "pw"}


def write_wav(path, seconds, tone_hz=None, rate=8000):
    """Synthetic mono 16-bit WAV (silence, or a sine tone); no personal data."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = bytearray()
    for i in range(int(rate * seconds)):
        v = int(6000 * math.sin(2 * math.pi * tone_hz * i / rate)) if tone_hz else 0
        frames += struct.pack("<h", v)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return path


@pytest.fixture
def example_cfg():
    return config.load(EXAMPLE)


@pytest.fixture
def cfg(tmp_path, example_cfg):
    paths = dataclasses.replace(example_cfg.paths, data_dir=tmp_path / "data", work_dir=tmp_path / "work",
                                generated_dir=tmp_path / "generated", overrides_dir=tmp_path / "overrides")
    spool = dataclasses.replace(example_cfg.spool, path=tmp_path / "spool")
    return dataclasses.replace(example_cfg, paths=paths, spool=spool)
