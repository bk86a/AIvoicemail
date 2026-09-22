"""Minimal stdlib HTTP helpers shared by the provider clients."""
import json
import urllib.error
import urllib.request
import uuid


class ProviderError(Exception):
    """A provider call failed. str() carries only the HTTP status or the network error type: an error
    body can echo the request (transcript, caller details), so it is kept privately in `_body` and
    never formatted into a message or log line. `reason` is the local network error text (no
    provider content), for `aivoicemail check`."""

    def __init__(self, message: str, status: int | None = None, *, body: bytes = b"", reason: str | None = None):
        super().__init__(message)
        self.status = status
        self._body = body
        self.reason = reason


def _send(req: urllib.request.Request, timeout: int) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        raise ProviderError(f"HTTP {e.code}", e.code, body=e.read()[:1000]) from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        detail = getattr(e, "reason", None) or e
        raise ProviderError(f"network error ({type(e).__name__})", reason=f"{type(e).__name__}: {detail}") from None


def post_json(url, payload, *, headers, timeout=60):
    h = {"Content-Type": "application/json", **headers}
    return _send(urllib.request.Request(url, data=json.dumps(payload).encode(), headers=h, method="POST"), timeout)


def post_multipart(url, fields, files, *, headers, timeout=120):
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    for name, (filename, content, mime) in files.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: {mime}\r\n\r\n".encode() + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    h = {"Content-Type": f"multipart/form-data; boundary={boundary}", **headers}
    return _send(urllib.request.Request(url, data=b"".join(parts), headers=h, method="POST"), timeout)


def post_bytes(url, data, *, headers, timeout=60):
    return _send(urllib.request.Request(url, data=data, headers=dict(headers), method="POST"), timeout)


def get(url, *, headers, timeout=15):
    return _send(urllib.request.Request(url, headers=dict(headers), method="GET"), timeout)


def auth_headers(auth: str, key: str | None) -> dict[str, str]:
    if key is None:
        return {}
    return {"api-key": key} if auth == "api-key" else {"Authorization": f"Bearer {key}"}
