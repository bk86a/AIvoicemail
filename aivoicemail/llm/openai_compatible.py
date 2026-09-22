"""OpenAI-compatible /chat/completions: Mistral, OpenAI, Azure OpenAI (v1), Ollama, vLLM, others."""
import json

from .. import http
from .schema import SCHEMA, build_prompt, parse_summary


def summarise(endpoint, key, transcript, meta, email_language):
    system, user = build_prompt(transcript, meta, email_language)
    payload = {"model": endpoint.model,
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if endpoint.structured == "json_schema":
        payload["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "voicemail_summary", "schema": SCHEMA, "strict": True}}
    elif endpoint.structured == "json_object":
        payload["response_format"] = {"type": "json_object"}
    _, body = http.post_json(endpoint.base_url.rstrip("/") + "/chat/completions", payload,
                             headers=http.auth_headers(endpoint.auth, key))
    return parse_summary(json.loads(body)["choices"][0]["message"]["content"], meta)
