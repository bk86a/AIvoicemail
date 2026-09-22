"""8 kHz mono 16-bit PCM helpers for prompt rendering."""
import io
import math
import struct
import wave

RATE = 8000


def silence(ms: int) -> bytes:
    return b"\0\0" * (RATE * ms // 1000)


def tone(ms: int, freq: float, amplitude: int = 8000) -> bytes:
    n = RATE * ms // 1000
    return b"".join(struct.pack("<h", int(amplitude * math.sin(2 * math.pi * freq * i / RATE))) for i in range(n))


def wav_bytes(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def check_format(path) -> str | None:
    try:
        with wave.open(str(path), "rb") as w:
            channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
    except (wave.Error, EOFError, OSError) as e:
        return f"not a readable WAV ({e})"
    if (channels, width, rate) != (1, 2, RATE):
        return f"must be 8 kHz mono 16-bit, got {rate} Hz, {channels} channel(s), {8 * width}-bit"
    if frames == 0:
        return "empty"
    return None


def duration(path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def resample(pcm: bytes, rate: int) -> bytes:
    """Mono signed 16-bit PCM at `rate` -> 8 kHz (libswresample via PyAV, a faster-whisper dependency)."""
    import av
    import numpy as np
    frame = av.AudioFrame.from_ndarray(np.frombuffer(pcm, dtype="<i2").reshape(1, -1), format="s16", layout="mono")
    frame.sample_rate = rate
    resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
    out = bytearray()
    for f in resampler.resample(frame) + resampler.resample(None):
        out += f.to_ndarray().astype("<i2").tobytes()
    return bytes(out)
