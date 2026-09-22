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


def test_http_error_keeps_body_private_and_never_in_str(monkeypatch):
    body = b'{"error": "bad request: caller +32470123456 said Goedendag, bel mij terug"}'

    def fake(req, timeout):
        raise urllib.error.HTTPError("https://x", 400, "bad", {}, io.BytesIO(body))

    monkeypatch.setattr(http.urllib.request, "urlopen", fake)
    with pytest.raises(http.ProviderError) as e:
        http.post_json("https://x", {}, headers={})
    assert e.value.status == 400 and str(e.value) == "HTTP 400"
    assert "+32470123456" not in repr(e.value) and "Goedendag" not in repr(e.value)
    assert b"+32470123456" in e.value._body  # kept for debugging, never formatted


def test_network_error(monkeypatch):
    def fake(req, timeout):
        raise urllib.error.URLError("unreachable")

    monkeypatch.setattr(http.urllib.request, "urlopen", fake)
    with pytest.raises(http.ProviderError) as e:
        http.get("https://x", headers={})
    assert e.value.status is None and str(e.value) == "network error (URLError)"
    assert e.value.reason == "URLError: unreachable"


def test_auth_headers():
    assert http.auth_headers("bearer", "k") == {"Authorization": "Bearer k"}
    assert http.auth_headers("api-key", "k") == {"api-key": "k"}
    assert http.auth_headers("bearer", None) == {}
