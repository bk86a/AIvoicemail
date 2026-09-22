"""Size SIPp scenario pauses from the rendered prompt WAVs and render the call scenarios.

Each tone pause must outlast the prompts Asterisk plays before Record() (generated dialplan:
Wait(0.5), menu, WaitExten(5), notice, beep) and then stream RECORD_S of tone into the recording.

Usage: python -m aivoicemail.sipp.timings --sounds DIR --out DIR --menu-line ID --single-line ID [--work /work]
"""
import argparse
import math
import shutil
import wave
from pathlib import Path

from .pcap import TONE_S

WAIT_S = 0.5    # Wait(0.5) after Answer()
EXTEN_S = 5     # WaitExten(5) after the menu
RECORD_S = 20   # seconds of tone streamed into Record() before BYE
BEEP_S = 1.0    # Record() beep plus margin before speech starts
HANGUP_MS = 2000
SCENARIOS = Path(__file__).resolve().parent / "scenarios"
STATIC = ("unknown_did.xml", "unidentified.xml", "callerid_injection.xml")
SDP = {
    True: ("      m=audio [media_port] RTP/AVP 8 101\n      a=rtpmap:8 PCMA/8000\n"
           "      a=rtpmap:101 telephone-event/8000\n      a=fmtp:101 0-16"),
    False: "      m=audio [media_port] RTP/AVP 8\n      a=rtpmap:8 PCMA/8000",
}


def durations(sounds_dir) -> dict[str, float]:
    out = {}
    for f in sorted(Path(sounds_dir).glob("*.wav")):
        with wave.open(str(f)) as w:
            out[f.stem] = w.getnframes() / w.getframerate()
    return out


def _ms(seconds) -> int:
    ms = int(math.ceil(seconds)) * 1000
    if ms > (TONE_S - 5) * 1000:
        raise ValueError(f"pause {ms} ms exceeds the {TONE_S} s tone clips; shorten the prompts")
    return ms


def dtmf_post_ms(d, line_id) -> int:
    """After a menu key: the longest single-language notice, then RECORD_S of recording."""
    notices = [v for k, v in d.items() if k.startswith(f"{line_id}-notice-") and not k.endswith("-auto")]
    return _ms(max(notices) + RECORD_S)


def timeout_ms(d, line_id) -> int:
    """No key: menu, WaitExten, the multilingual short notice, then RECORD_S of recording."""
    return _ms(WAIT_S + d[f"{line_id}-menu"] + EXTEN_S + d[f"{line_id}-notice-auto"] + RECORD_S)


def single_ms(d, line_id) -> int:
    [notice] = [v for k, v in d.items() if k.startswith(f"{line_id}-notice-")]
    return _ms(WAIT_S + notice + RECORD_S)


def media_block(mode, pause_ms, work="/work") -> str:
    if mode == "plain":
        return (f'<nop><action><exec play_pcap_audio="{work}/tone.pcap"/></action></nop>\n'
                f'  <pause milliseconds="{pause_ms}"/>')
    digit_clip = ("/usr/share/sip-tester/dtmf_2833_[field0].pcap" if mode == "rfc4733"
                  else f"{work}/inband_[field0].pcap")
    return (f'<nop><action><exec play_pcap_audio="{work}/tone_pre.pcap"/></action></nop>\n'
            '  <pause milliseconds="2000"/>\n'
            f'  <nop><action><exec play_pcap_audio="{digit_clip}"/></action></nop>\n'
            '  <pause milliseconds="1000"/>\n'
            f'  <nop><action><exec play_pcap_audio="{work}/tone_post.pcap"/></action></nop>\n'
            f'  <pause milliseconds="{pause_ms}"/>')


def render_call(mode, pause_ms, work="/work") -> str:
    """mode: "rfc4733" (telephone-event offered, SIPp's bundled RFC 4733 clip), "inband" (PCMA only,
    dual-tone digit in the audio) or "plain" (no key, tone for pause_ms)."""
    if mode not in ("rfc4733", "inband", "plain"):
        raise ValueError(mode)
    text = (SCENARIOS / "call.xml.in").read_text(encoding="utf-8")
    text = (text.replace("{{name}}", f"call_{mode}").replace("{{sdp}}", SDP[mode != "inband"])
            .replace("{{media}}", media_block(mode, pause_ms, work)))
    if "{{" in text:
        raise ValueError("unfilled placeholder in call.xml.in")
    return text


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sounds", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--menu-line", required=True)
    ap.add_argument("--single-line", required=True)
    ap.add_argument("--work", default="/work", help="directory of the pcaps as SIPp sees it")
    a = ap.parse_args(argv)
    d = durations(a.sounds)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    plan = {
        "call_rfc4733": ("rfc4733", dtmf_post_ms(d, a.menu_line)),
        "call_inband": ("inband", dtmf_post_ms(d, a.menu_line)),
        "call_timeout": ("plain", timeout_ms(d, a.menu_line)),
        "call_single": ("plain", single_ms(d, a.single_line)),
        "call_hangup": ("plain", HANGUP_MS),
    }
    for name, (mode, ms) in plan.items():
        (out / f"{name}.xml").write_text(render_call(mode, ms, a.work), encoding="utf-8")
        print(f"{name}: {mode}, {ms} ms")
    for name in STATIC:
        shutil.copyfile(SCENARIOS / name, out / name)


if __name__ == "__main__":
    main()
