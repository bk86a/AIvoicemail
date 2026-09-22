import math
import struct
import wave

from aivoicemail.sipp import pcap

HDR = 16 + 14 + 20 + 8 + 12  # pcap record header, eth, ip, udp, rtp


def packets(path):
    data = path.read_bytes()[24:]
    out = []
    while data:
        _, _, incl, _ = struct.unpack("<IIII", data[:16])
        frame = data[16:16 + incl]
        _, pt, seq, ts, ssrc = struct.unpack("!BBHII", frame[42:54])
        out.append((pt, seq, ts, ssrc, frame[54:]))
        data = data[16 + incl:]
    return out


def goertzel(samples, freq):
    k = 2 * math.cos(2 * math.pi * freq / pcap.RATE)
    s1 = s2 = 0.0
    for x in samples:
        s1, s2 = x + k * s1 - s2, s1
    return s1 * s1 + s2 * s2 - k * s1 * s2


def test_wav_with_leading_silence(tmp_path):
    samples = [(i * 97) % 20000 - 10000 for i in range(800)]  # 0.1 s at 8 kHz
    wav = tmp_path / "in.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    out = tmp_path / "out.pcap"
    pcap.main([str(out), "0", "--wav", str(wav), "--lead", "0.04", "--seq", "500"])
    pk = packets(out)
    assert len(pk) == 2 + 5
    assert [p[1] for p in pk] == list(range(500, 507))
    assert [p[2] for p in pk] == [i * 160 for i in range(7)]
    assert all(p[0] == 8 for p in pk)
    assert pk[0][4] == pk[1][4] == bytes([0xD5]) * 160
    assert b"".join(p[4] for p in pk[2:]) == bytes(pcap.alaw(s) for s in samples)


def test_tone_packets(tmp_path):
    out = tmp_path / "tone.pcap"
    pcap.main([str(out), "0.1"])
    pk = packets(out)
    assert len(pk) == 5 and pk[0][1] == 1000 and pk[0][3] == 0x5A5A0001


def test_dtmf_digit_has_both_frequencies():
    s = pcap.dtmf_samples("5")
    assert len(s) == 8 * (200 + 150 + 300)
    burst = s[1600:1600 + 1200]
    on = min(goertzel(burst, 770), goertzel(burst, 1336))
    off = max(goertzel(burst, f) for f in (697, 852, 941, 1209, 1477))
    assert on > 50 * off


def test_write_media(tmp_path):
    pcap.write_media(tmp_path, digits="12")
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["inband_1.pcap", "inband_2.pcap", "tone.pcap", "tone_post.pcap", "tone_pre.pcap"]
    assert len(packets(tmp_path / "tone_pre.pcap")) == 150
    assert packets(tmp_path / "tone_post.pcap")[0][1:4:2] == (20000, 0x5A5A0002)
    assert len(packets(tmp_path / "inband_1.pcap")) == 33
