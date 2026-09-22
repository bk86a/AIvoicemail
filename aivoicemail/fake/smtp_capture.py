"""Minimal local SMTP server that stores each message as <dir>/<time_ns>-<n>.eml (fake mode, tests)."""
import email
import email.policy
import itertools
import socketserver
import threading
import time
from pathlib import Path


class _Handler(socketserver.StreamRequestHandler):
    def _w(self, line: str) -> None:
        self.wfile.write(line.encode("ascii") + b"\r\n")

    def handle(self) -> None:
        self._w("220 aivoicemail-capture ESMTP")
        while True:
            raw = self.rfile.readline(65536)
            if not raw:
                return
            verb = raw.decode("ascii", "replace").strip().split(" ", 1)[0].upper()
            if verb == "EHLO":
                self.wfile.write(b"250-aivoicemail-capture\r\n250 8BITMIME\r\n")
            elif verb == "HELO":
                self._w("250 aivoicemail-capture")
            elif verb in ("MAIL", "RCPT", "RSET", "NOOP"):
                self._w("250 OK")
            elif verb == "DATA":
                self._w("354 End data with <CR><LF>.<CR><LF>")
                data = bytearray()
                while True:
                    line = self.rfile.readline(1 << 20)
                    if not line or line in (b".\r\n", b".\n"):
                        break
                    data += line[1:] if line.startswith(b"..") else line
                self.server.store(bytes(data))
                self._w("250 OK")
            elif verb == "QUIT":
                self._w("221 Bye")
                return
            else:
                self._w("502 Not implemented")


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class CaptureServer:
    def __init__(self, directory, host="127.0.0.1", port=0):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._server = _Server((host, port), _Handler)
        self._server.store = self._store
        self._lock = threading.Lock()
        self._seq = itertools.count(1)

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def _store(self, data: bytes) -> None:
        with self._lock:  # stored with LF line endings, like a local mailbox file
            (self.directory / f"{time.time_ns()}-{next(self._seq)}.eml").write_bytes(data.replace(b"\r\n", b"\n"))

    def start(self) -> int:
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self.port

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def messages(directory):
    return [email.message_from_bytes(p.read_bytes(), policy=email.policy.default)
            for p in sorted(Path(directory).glob("*.eml"))]
