import io
import urllib.error

import pytest

from aivoicemail import http


class Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def test_multipart_contains_file(monkeypatch):
    seen = {}

    def fake(req, timeout):
        seen["body"], seen["ctype"] = req.data, req.headers["Content-type"]
        return Resp(b"{}")

    monkeypatch.setattr(http.urllib.request, "urlopen", fake)
    http.post_multipart("https://x", {"model": "m"}, {"file": ("a.wav", b"RIFF", "audio/wav")}, headers={})
    assert seen["ctype"].startswith("multipart/form-data; boundary=")
    assert b'name="file"; filename="a.wav"' in seen["body"] and b"RIFF" in seen["body"]


def test_post_bytes_and_get(monkeypatch):
    seen = []

    def fake(req, timeout):
        seen.append((req.get_method(), req.data, dict(req.headers)))
        return Resp(b"ok")

    monkeypatch.setattr(http.urllib.request, "urlopen", fake)
    assert http.post_bytes("https://x", b"<speak/>", headers={"X-A": "1"}) == (200, b"ok")
    assert http.get("https://x/models", headers={"X-B": "2"}) == (200, b"ok")
    assert seen[0][0] == "POST" and seen[0][1] == b"<speak/>" and seen[0][2]["X-a"] == "1"
    assert seen[1][0] == "GET" and seen[1][2]["X-b"] == "2"


def test_http_error_raises_provider_error(monkeypatch):
    def fake(req, timeout):
        raise urllib.error.HTTPError("https://x", 500, "boom", {}, io.BytesIO(b"err"))

    monkeypatch.setattr(http.urllib.request, "urlopen", fake)
    with pytest.raises(http.ProviderError) as e:
        http.post_json("https://x", {}, headers={})
    assert e.value.status == 500


def test_http_error_message_keeps_status_and_at_most_100_body_bytes(monkeypatch):
    body = b"A" * 100 + b"SECRET-TAIL" * 20

    def fake(req, timeout):
        raise urllib.error.HTTPError("https://x", 429, "slow", {}, io.BytesIO(body))

    monkeypatch.setattr(http.urllib.request, "urlopen", fake)
    with pytest.raises(http.ProviderError) as e:
        http.post_json("https://x", {}, headers={})
    msg = str(e.value)
    assert e.value.status == 429 and "HTTP 429" in msg
    assert "A" * 100 in msg and "SECRET" not in msg


def test_network_error(monkeypatch):
    def fake(req, timeout):
        raise urllib.error.URLError("unreachable")

    monkeypatch.setattr(http.urllib.request, "urlopen", fake)
    with pytest.raises(http.ProviderError) as e:
        http.get("https://x", headers={})
    assert e.value.status is None


def test_auth_headers():
    assert http.auth_headers("bearer", "k") == {"Authorization": "Bearer k"}
    assert http.auth_headers("api-key", "k") == {"api-key": "k"}
    assert http.auth_headers("bearer", None) == {}
