"""OpenAI-compatible /audio/transcriptions (Mistral Voxtral, OpenAI, local gateways)."""
import json
from pathlib import Path

from .. import http
from .base import nonempty


def transcribe(endpoint, key, wav, lang) -> str:
    wav = Path(wav)
    fields = {"model": endpoint.model}
    if lang != "auto":
        fields["language"] = lang
    _, body = http.post_multipart(endpoint.base_url.rstrip("/") + "/audio/transcriptions", fields,
                                  {"file": (wav.name, wav.read_bytes(), "audio/wav")},
                                  headers=http.auth_headers(endpoint.auth, key))
    return nonempty(json.loads(body).get("text"))
