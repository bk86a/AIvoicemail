"""G.711 A-law (RTP PT 8) pcaps for SIPp play_pcap_audio: tone, speech WAV, in-band DTMF digits.

SIPp replays pcap RTP verbatim (no sequence/timestamp rewrite), so looping a short clip resends old
sequence numbers that Asterisk discards: every media segment is one continuous clip.

Usage: python -m aivoicemail.sipp.pcap --media DIR [--digits 123] [--wav IN.wav --lead S]
       python -m aivoicemail.sipp.pcap OUT.pcap SECONDS [--seq N] [--ssrc N] [--freq HZ] [--wav IN.wav --lead S] [--dtmf D]
"""
import argparse
import math
import struct
import wave
from pathlib import Path

RATE = 8000
PTIME_MS = 20
SPP = RATE * PTIME_MS // 1000  # samples per packet
TONE_S = 120                   # tone.pcap / tone_post.pcap length; scenario pauses must stay below
DTMF = {"1": (697, 1209), "2": (697, 1336), "3": (697, 1477), "4": (770, 1209), "5": (770, 1336),
        "6": (770, 1477), "7": (852, 1209), "8": (852, 1336), "9": (852, 1477), "*": (941, 1209),
        "0": (941, 1336), "#": (941, 1477)}


def alaw(sample: int) -> int:
    """G.711 A-law encode one signed 16-bit sample."""
    sign = 0x80 if sample >= 0 else 0x00
    if sample < 0:
        sample = -sample - 1
    sample = min(sample, 32767) >> 3
    if sample < 32:
        code = sample >> 1
    else:
        exp = sample.bit_length() - 5
        code = (exp << 4) | ((sample >> exp) & 0x0F)
    return (code | sign) ^ 0x55


def tone_samples(seconds, freq=440.0, amplitude=8000) -> list[int]:
    return [int(amplitude * math.sin(2 * math.pi * freq * n / RATE)) for n in range(int(round(seconds * RATE)))]


def dtmf_samples(digit, *, lead_ms=200, tone_ms=150, tail_ms=300, amplitude=5000) -> list[int]:
    low, high = DTMF[digit]
    tone = [int(amplitude * (math.sin(2 * math.pi * low * n / RATE) + math.sin(2 * math.pi * high * n / RATE)))
            for n in range(RATE * tone_ms // 1000)]
    return [0] * (RATE * lead_ms // 1000) + tone + [0] * (RATE * tail_ms // 1000)


def wav_samples(path, lead=0.0) -> list[int]:
    with wave.open(str(path), "rb") as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, RATE):
            raise SystemExit("--wav must be 8 kHz mono 16-bit")
        raw = w.readframes(w.getnframes())
    return [0] * int(round(lead * RATE)) + list(struct.unpack(f"<{len(raw) // 2}h", raw))


def write_pcap(path, samples, *, seq=1000, ssrc=0x5A5A0001) -> int:
    samples = list(samples) + [0] * (-len(samples) % SPP)
    audio = bytes(alaw(v) for v in samples)
    npkts = len(audio) // SPP
    with open(path, "wb") as f:
        f.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
        for i in range(npkts):
            payload = audio[i * SPP:(i + 1) * SPP]
            rtp = struct.pack("!BBHII", 0x80, 8, (seq + i) & 0xFFFF, (i * SPP) & 0xFFFFFFFF, ssrc)
            udp_len = 8 + len(rtp) + len(payload)
            udp = struct.pack("!HHHH", 6000, 6000, udp_len, 0)
            ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + udp_len, 0, 0, 64, 17, 0,
                             bytes([127, 0, 0, 1]), bytes([127, 0, 0, 1]))
            frame = b"\x00" * 12 + b"\x08\x00" + ip + udp + rtp + payload
            t = i * PTIME_MS
            f.write(struct.pack("<IIII", t // 1000, (t % 1000) * 1000, len(frame), len(frame)))
            f.write(frame)
    return npkts


def write_media(work, digits="123", wav=None, lead=0.0) -> None:
    """The clips the call scenarios play: tone.pcap and tone_post.pcap (TONE_S of tone, or the speech
    WAV after `lead` seconds of silence), tone_pre.pcap (3 s) and inband_<digit>.pcap per digit."""
    work = Path(work)
    main = wav_samples(wav, lead) if wav else tone_samples(TONE_S)
    write_pcap(work / "tone.pcap", main)
    write_pcap(work / "tone_pre.pcap", tone_samples(3))
    write_pcap(work / "tone_post.pcap", main, seq=20000, ssrc=0x5A5A0002)
    for digit in digits:
        write_pcap(work / f"inband_{digit}.pcap", dtmf_samples(digit), seq=10000, ssrc=0x5A5A0003)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?")
    ap.add_argument("seconds", nargs="?", type=float, default=0.0)
    ap.add_argument("--media", help="write every scenario clip into this directory")
    ap.add_argument("--digits", default="123")
    ap.add_argument("--seq", type=lambda v: int(v, 0), default=1000)
    ap.add_argument("--ssrc", type=lambda v: int(v, 0), default=0x5A5A0001)
    ap.add_argument("--freq", type=float, default=440.0)
    ap.add_argument("--wav")
    ap.add_argument("--lead", type=float, default=0.0)
    ap.add_argument("--dtmf", choices=sorted(DTMF))
    a = ap.parse_args(argv)
    if a.media:
        write_media(a.media, a.digits, a.wav, a.lead)
        return
    if not a.out:
        ap.error("OUT or --media is required")
    if a.dtmf:
        samples = dtmf_samples(a.dtmf)
    elif a.wav:
        samples = wav_samples(a.wav, a.lead)
    else:
        samples = tone_samples(a.seconds, a.freq)
    write_pcap(a.out, samples, seq=a.seq, ssrc=a.ssrc)


if __name__ == "__main__":
    main()
