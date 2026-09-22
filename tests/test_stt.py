import json
import sys
import types

from aivoicemail import stt
from aivoicemail.config import Endpoint, Stt
from aivoicemail.stt.whisper_local import WhisperLocal

REMOTE = Endpoint(name="remote", model="voxtral-mini-latest", base_url="https://stt.example/v1", key_env="STT_API_KEY")
CFG = Stt(chain=("whisper_local", "remote"), endpoints={"remote": REMOTE})
QUIET = dict(sleep=lambda s: None, log=lambda *a: None)


class FakeWhisper:
    def __init__(self, text=None, error=None):
        self.text, self.error, self.calls = text, error, []

    def transcribe(self, path, lang, candidates):
        self.calls.append((lang, tuple(candidates)))
        if self.error:
            raise self.error
        return self.text


def wav(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(b"RIFF0000WAVE")
    return p


def fake_post(monkeypatch, text="Bonjour"):
    seen = {}

    def post_multipart(url, fields, files, *, headers, timeout=120):
        seen.update(url=url, fields=fields, files=files, headers=headers)
        return 200, json.dumps({"text": text}).encode()

    monkeypatch.setattr(stt.openai_compatible.http, "post_multipart", post_multipart)
    return seen


def test_local_first(tmp_path):
    w = FakeWhisper("Dzień dobry")
    out = stt.transcribe(wav(tmp_path), "pl", ("pl",), cfg=CFG, env={"STT_API_KEY": "k"}, whisper=w, **QUIET)
    assert out == ("Dzień dobry", "whisper_local")
    assert w.calls == [("pl", ("pl",))]


def test_empty_local_falls_to_remote(tmp_path, monkeypatch):
    seen = fake_post(monkeypatch)
    out = stt.transcribe(wav(tmp_path), "fr", ("nl", "fr", "en"), cfg=CFG, env={"STT_API_KEY": "key"},
                         whisper=FakeWhisper("   "), **QUIET)
    assert out == ("Bonjour", "remote")
    assert seen["url"] == "https://stt.example/v1/audio/transcriptions"
    assert seen["fields"] == {"model": "voxtral-mini-latest", "language": "fr"}
    assert seen["headers"] == {"Authorization": "Bearer key"}
    assert seen["files"]["file"][0] == "a.wav"


def test_auto_language_not_sent_to_remote(tmp_path, monkeypatch):
    seen = fake_post(monkeypatch, "Hello")
    stt.transcribe(wav(tmp_path), "auto", ("nl", "fr", "en"), cfg=CFG, env={"STT_API_KEY": "k"},
                   whisper=FakeWhisper(error=RuntimeError("oom")), **QUIET)
    assert seen["fields"] == {"model": "voxtral-mini-latest"}


def test_remote_without_key_env_sends_no_auth(tmp_path, monkeypatch):
    seen = fake_post(monkeypatch)
    cfg = Stt(chain=("local-gw",), endpoints={"local-gw": Endpoint(name="local-gw", model="m", base_url="http://127.0.0.1:8000/v1/")})
    assert stt.transcribe(wav(tmp_path), "fr", (), cfg=cfg, env={}, whisper=None, **QUIET) == ("Bonjour", "local-gw")
    assert seen["headers"] == {} and seen["url"] == "http://127.0.0.1:8000/v1/audio/transcriptions"


def test_all_fail_or_skip(tmp_path):
    out = stt.transcribe(wav(tmp_path), "nl", ("nl",), cfg=CFG, env={}, whisper=FakeWhisper(error=RuntimeError("x")), **QUIET)
    assert out is None


def test_unloaded_local_engine_is_skipped(tmp_path, monkeypatch):
    fake_post(monkeypatch, "Hallo")
    out = stt.transcribe(wav(tmp_path), "nl", ("nl",), cfg=CFG, env={"STT_API_KEY": "k"}, whisper=None, **QUIET)
    assert out == ("Hallo", "remote")


def fake_faster_whisper(monkeypatch, probs):
    calls = {}
    mod = types.ModuleType("faster_whisper")
    mod.decode_audio = lambda path, sampling_rate: [0.0] * sampling_rate

    class WhisperModel:
        def __init__(self, name, **kw):
            calls["init"] = (name, kw)

        def detect_language(self, audio):
            return probs[0][0], probs[0][1], probs

        def transcribe(self, audio, language, beam_size, vad_filter):
            calls["language"] = language
            return [types.SimpleNamespace(text=" Hallo "), types.SimpleNamespace(text="daar ")], None

    mod.WhisperModel = WhisperModel
    vad = types.ModuleType("faster_whisper.vad")
    vad.get_speech_timestamps = lambda audio: [{"start": 0, "end": 16000}, {"start": 32000, "end": 40000}]
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    monkeypatch.setitem(sys.modules, "faster_whisper.vad", vad)
    return calls


def test_whisper_auto_restricted_to_candidates(tmp_path, monkeypatch):
    calls = fake_faster_whisper(monkeypatch, [("de", 0.6), ("nl", 0.3), ("fr", 0.1)])
    w = WhisperLocal("small", 2, tmp_path / "models")
    assert "init" not in calls  # lazy
    assert w.transcribe(tmp_path / "a.wav", "auto", ("nl", "fr", "en")) == "Hallo daar"
    assert calls["language"] == "nl"
    assert calls["init"] == ("small", {"device": "cpu", "compute_type": "int8", "cpu_threads": 2,
                                       "download_root": str(tmp_path / "models")})


def test_whisper_explicit_language_and_speech_seconds(tmp_path, monkeypatch):
    calls = fake_faster_whisper(monkeypatch, [("de", 0.9)])
    w = WhisperLocal("small", 2, tmp_path)
    w.transcribe(tmp_path / "a.wav", "fr", ("fr",))
    assert calls["language"] == "fr"
    assert w.speech_seconds(tmp_path / "a.wav") == 1.5
