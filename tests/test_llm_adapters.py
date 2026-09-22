import json
import sys
import types

import pytest

from aivoicemail import llm, retry
from aivoicemail.config import Endpoint, Llm
from aivoicemail.llm import anthropic as anthropic_adapter
from aivoicemail.llm import openai_compatible, schema

META = {"id": "x", "line": "be", "caller": "+32470123456", "lang_choice": "nl", "did": "3220000001",
        "started_at": "t", "ended_at": "t", "duration_s": 30, "has_audio": True}
GOOD = {"caller_name": "Jan", "company": None, "subject": "Offerte", "callback_number": None,
        "language": "nl", "urgency": "normal", "summary": "Vraagt offerte.", "requested_action": None}
QUIET = dict(sleep=lambda s: None, log=lambda *a: None)


def ep(**kw):
    return Endpoint(**{"name": "primary", "model": "m-small", "base_url": "https://llm.example/v1/", "key_env": "LLM_API_KEY", **kw})


def fake_post(monkeypatch, content):
    seen = {}

    def post_json(url, payload, *, headers, timeout=60):
        seen.update(url=url, payload=payload, headers=headers)
        return 200, json.dumps({"choices": [{"message": {"content": content}}]}).encode()

    monkeypatch.setattr(openai_compatible.http, "post_json", post_json)
    return seen


def test_json_schema_request_shape(monkeypatch):
    seen = fake_post(monkeypatch, json.dumps(GOOD))
    assert openai_compatible.summarise(ep(), "key", "t", META, "en") == GOOD
    assert seen["url"] == "https://llm.example/v1/chat/completions"
    assert seen["payload"]["model"] == "m-small"
    assert seen["payload"]["response_format"] == {
        "type": "json_schema", "json_schema": {"name": "voicemail_summary", "schema": schema.SCHEMA, "strict": True}}
    system, user = schema.build_prompt("t", META, "en")
    assert seen["payload"]["messages"] == [{"role": "system", "content": system}, {"role": "user", "content": user}]
    assert seen["headers"] == {"Authorization": "Bearer key"}


def test_json_object_and_none_modes(monkeypatch):
    seen = fake_post(monkeypatch, json.dumps(GOOD))
    openai_compatible.summarise(ep(structured="json_object"), "k", "t", META, "en")
    assert seen["payload"]["response_format"] == {"type": "json_object"}
    seen = fake_post(monkeypatch, "Here you go: " + json.dumps(GOOD))
    assert openai_compatible.summarise(ep(structured="none"), "k", "t", META, "en") == GOOD
    assert "response_format" not in seen["payload"]


def test_azure_style_api_key_header(monkeypatch):
    seen = fake_post(monkeypatch, json.dumps(GOOD))
    openai_compatible.summarise(ep(auth="api-key"), "k", "t", META, "en")
    assert seen["headers"] == {"api-key": "k"}


def test_chain_falls_back_on_invalid_output(monkeypatch):
    calls = []

    def fake_summarise(endpoint, key, transcript, meta, email_language):
        calls.append(endpoint.name)
        if endpoint.name == "primary":
            return dict(GOOD, urgency="asap")
        return dict(GOOD)

    monkeypatch.setattr(openai_compatible, "summarise", fake_summarise)
    cfg = Llm(chain=("primary", "secondary"), endpoints={"primary": ep(), "secondary": ep(name="secondary")})
    out = llm.summarise("t", META, email_language="en", cfg=cfg, env={"LLM_API_KEY": "k"}, **QUIET)
    assert out == (GOOD, "secondary") and calls == ["primary"] * 3 + ["secondary"]


def test_missing_key_skips_every_provider():
    cfg = Llm(chain=("primary",), endpoints={"primary": ep()})
    assert llm.summarise("t", META, email_language="en", cfg=cfg, env={}, **QUIET) is None


def fake_anthropic(monkeypatch, response):
    seen = {}
    mod = types.ModuleType("anthropic")

    class Messages:
        def create(self, **kw):
            seen.update(kw)
            return response

    class Anthropic:
        def __init__(self, **kw):
            seen["client"] = kw
            self.messages = Messages()

    mod.Anthropic = Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    return seen


def text_response(text, stop="end_turn"):
    return types.SimpleNamespace(stop_reason=stop, content=[types.SimpleNamespace(type="text", text=text)])


def test_anthropic_request_shape(monkeypatch):
    seen = fake_anthropic(monkeypatch, text_response(json.dumps(GOOD)))
    e = ep(kind="anthropic", base_url="", model="claude-model", key_env="ANTHROPIC_API_KEY")
    assert anthropic_adapter.summarise(e, "akey", "t", META, "en") == GOOD
    system, user = schema.build_prompt("t", META, "en")
    assert seen["client"] == {"api_key": "akey", "max_retries": 0, "timeout": 120}
    assert seen["model"] == "claude-model" and seen["system"] == system
    assert seen["messages"] == [{"role": "user", "content": user}]
    assert seen["output_config"] == {"format": {"type": "json_schema", "schema": schema.SCHEMA}}


def test_anthropic_refusal_is_failure(monkeypatch):
    fake_anthropic(monkeypatch, text_response("{}", stop="refusal"))
    with pytest.raises(RuntimeError):
        anthropic_adapter.summarise(ep(kind="anthropic"), "k", "t", META, "en")


def test_anthropic_extra_missing_skips(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(retry.Skip):
        anthropic_adapter.summarise(ep(kind="anthropic"), "k", "t", META, "en")


def test_anthropic_without_key_skips(monkeypatch):
    fake_anthropic(monkeypatch, text_response(json.dumps(GOOD)))
    with pytest.raises(retry.Skip):
        anthropic_adapter.summarise(ep(kind="anthropic", key_env=None), None, "t", META, "en")
