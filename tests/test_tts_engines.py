import io
import wave
import xml.etree.ElementTree as ET

import pytest

from aivoicemail.prompts import Prompt, Segment
from aivoicemail.tts.azure import AzureEngine
from aivoicemail.tts.piper import PiperEngine
from aivoicemail.tts.placeholder import PlaceholderEngine

SYN = "{http://www.w3.org/2001/10/synthesis}"
MSTTS = "{https://www.w3.org/2001/mstts}"
VOICES = {"nl": "nl-BE-DenaNeural", "fr": "fr-BE-CharlineNeural", "en": "en-GB-SoniaNeural", "pl": "pl-PL-AgnieszkaNeural"}
PRON = {"ACME": {"nl": "ˈaː.kmə", "en": "ˈæk.mi"}}


def prompt(*segs, sentence_ms=None):
    return Prompt("p", "be", "notice", tuple(segs), sentence_ms)


def frames(wav):
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 8000)
        return w.getnframes()


def azure():
    return AzureEngine("westeurope", "k", VOICES, PRON)


def test_ssml_structure_and_exact_silences():
    root = ET.fromstring(azure().ssml(prompt(Segment("nl", "ACME."), Segment("fr", "Bonjour."))))
    assert root.tag == f"{SYN}speak" and root.get("{http://www.w3.org/XML/1998/namespace}lang") == "nl-BE"
    voices = root.findall(f"{SYN}voice")
    assert [v.get("name") for v in voices] == ["nl-BE-DenaNeural", "fr-BE-CharlineNeural"]
    for v in voices:
        assert [(s.get("type"), s.get("value")) for s in v.findall(f"{MSTTS}silence")] == [
            ("Leading-exact", "0ms"), ("Tailing-exact", "0ms")]
        assert list(v)[0].tag == f"{MSTTS}silence" and list(v)[-1].tag == f"{SYN}break"


def test_root_declares_mstts_namespace():
    assert 'xmlns:mstts="https://www.w3.org/2001/mstts"' in azure().ssml(prompt(Segment("en", "Hi.")))


def test_phoneme_per_language():
    e = azure()
    assert e.say(Segment("nl", "ACME BV.")) == '<phoneme alphabet="ipa" ph="ˈaː.kmə">ACME</phoneme> BV.'
    assert e.say(Segment("en", "ACME BV.")) == '<phoneme alphabet="ipa" ph="ˈæk.mi">ACME</phoneme> BV.'
    assert e.say(Segment("fr", "ACME BV.")) == "ACME BV."
    assert e.say(Segment("en", "ACMEX")) == "ACMEX"


def test_breaks_default_and_override():
    root = ET.fromstring(azure().ssml(prompt(Segment("en", "Hi.", 200), Segment("en", "Yo."))))
    assert [b.get("time") for b in root.iter(f"{SYN}break")] == ["200ms", "400ms"]


def test_sentence_boundary_silence_only_when_set():
    root = ET.fromstring(azure().ssml(prompt(Segment("pl", "Raz. Dwa."), sentence_ms=300)))
    kids = list(root.find(f"{SYN}voice"))
    assert (kids[2].tag, kids[2].get("type"), kids[2].get("value")) == (f"{MSTTS}silence", "Sentenceboundary-exact", "300ms")
    assert "Sentenceboundary" not in azure().ssml(prompt(Segment("pl", "Raz. Dwa.")))


def test_text_escaped():
    e = azure()
    assert "A &amp; B &lt;c&gt; " in e.say(Segment("nl", "A & B <c> ACME"))
    root = ET.fromstring(e.ssml(prompt(Segment("nl", "A & B <c> ACME"))))
    assert "".join(root.find(f"{SYN}voice/{SYN}lang").itertext()) == "A & B <c> ACME"


def test_azure_request():
    seen = {}

    def post(url, data, *, headers, timeout=60):
        seen.update(url=url, data=data, headers=headers)
        return 200, b"RIFF" + b"\0" * 40

    e = AzureEngine("westeurope", "secret", VOICES, PRON, post=post)
    assert e.render(prompt(Segment("en", "Hi."))).startswith(b"RIFF")
    assert seen["url"] == "https://westeurope.tts.speech.microsoft.com/cognitiveservices/v1"
    assert seen["headers"]["X-Microsoft-OutputFormat"] == "riff-8khz-16bit-mono-pcm"
    assert seen["headers"]["Ocp-Apim-Subscription-Key"] == "secret"
    assert seen["data"].startswith(b"<speak")


def test_azure_non_wav_response_fails():
    e = AzureEngine("r", "k", VOICES, post=lambda url, data, headers, timeout=60: (200, b"<error/>"))
    with pytest.raises(RuntimeError):
        e.render(prompt(Segment("en", "Hi.")))


def test_piper_pauses_pronunciation_and_voice_choice(tmp_path):
    calls = []

    def synth(voice, text):
        calls.append((voice, text))
        return [(8000, b"\1\0" * 800), (8000, b"\1\0" * 800)]  # two sentences of 0.1 s

    e = PiperEngine({"nl": "nl_BE-nathalie-medium", "en": "en_GB-alba-medium"}, tmp_path, PRON, synthesize=synth)
    wav = e.render(prompt(Segment("nl", "ACME. Dag."), Segment("en", "Bye.", 200), sentence_ms=300))
    assert calls == [("nl_BE-nathalie-medium", "[[ ˈaː.kmə ]]. Dag."), ("en_GB-alba-medium", "Bye.")]
    # per segment: 0.1 + 0.3 + 0.1 s speech/pause, then the break (400 ms default, 200 ms override)
    assert frames(wav) == (800 + 2400 + 800 + 3200) + (800 + 2400 + 800 + 1600)


def test_piper_downloads_missing_voice_once(tmp_path):
    downloads = []
    e = PiperEngine({"en": "en_GB-alba-medium"}, tmp_path, download=downloads.append, synthesize=lambda v, t: [])
    assert e.voice_path("en_GB-alba-medium") == tmp_path / "en_GB-alba-medium.onnx"
    assert downloads == ["en_GB-alba-medium"]
    (tmp_path / "en_GB-alba-medium.onnx").write_bytes(b"x")
    e.voice_path("en_GB-alba-medium")
    assert downloads == ["en_GB-alba-medium"]


def test_placeholder_is_deterministic():
    p = prompt(Segment("en", "abcd"), Segment("en", "ab", 200))
    wav = PlaceholderEngine().render(p)
    assert wav == PlaceholderEngine().render(p)
    assert frames(wav) == (1200 + 4 * 480 + 3200) + (1200 + 2 * 480 + 1600)
