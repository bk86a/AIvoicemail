import xml.etree.ElementTree as ET

import pytest

from aivoicemail.sipp import timings
from aivoicemail.tts import render_all
from aivoicemail.tts.placeholder import PlaceholderEngine

D = {"be-menu": 6.0, "be-notice-nl": 24.2, "be-notice-fr": 25.9, "be-notice-en": 22.0, "be-notice-auto": 28.4,
     "be-thanks-nl": 3.0, "pl-notice-pl": 26.1, "pl-thanks-pl": 3.0}


def test_pause_plan():
    assert timings.dtmf_post_ms(D, "be") == 46000            # ceil(25.9 + 20)
    assert timings.timeout_ms(D, "be") == 60000              # ceil(0.5 + 6 + 5 + 28.4 + 20)
    assert timings.single_ms(D, "pl") == 47000               # ceil(0.5 + 26.1 + 20)


def test_pause_longer_than_tone_clips_is_refused():
    with pytest.raises(ValueError):
        timings.timeout_ms(dict(D, **{"be-notice-auto": 100.0}), "be")


@pytest.mark.parametrize("mode,has_te,clip", [
    ("rfc4733", True, "/usr/share/sip-tester/dtmf_2833_[field0].pcap"),
    ("inband", False, "/work/inband_[field0].pcap"),
    ("plain", True, "/work/tone.pcap"),
])
def test_render_call(mode, has_te, clip):
    xml = timings.render_call(mode, 12000)
    root = ET.fromstring(xml)
    assert root.get("name") == f"call_{mode}"
    assert ("telephone-event" in xml) is has_te
    assert clip in xml and 'milliseconds="12000"' in xml
    assert [e.tag for e in root][-2:] == ["send", "recv"] and "BYE sip:" in xml


def test_render_call_rejects_unknown_mode():
    with pytest.raises(ValueError):
        timings.render_call("sip-info", 1000)


def test_main_writes_all_scenarios(cfg, tmp_path):
    render_all(cfg, {}, tmp_path / "gen", engine=PlaceholderEngine(), log=lambda *a: None)
    out = tmp_path / "sipp"
    timings.main(["--sounds", str(tmp_path / "gen" / "sounds" / "vm"), "--out", str(out),
                  "--menu-line", "be", "--single-line", "pl"])
    assert sorted(p.name for p in out.iterdir()) == [
        "call_hangup.xml", "call_inband.xml", "call_rfc4733.xml", "call_single.xml", "call_timeout.xml",
        "callerid_injection.xml", "unidentified.xml", "unknown_did.xml"]
    for p in out.iterdir():
        ET.fromstring(p.read_text())
