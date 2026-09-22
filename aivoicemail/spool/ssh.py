"""SSH spool backend (split mode): talks to the vm-spool forced command on the telephony host."""
import io
import json
import subprocess
import tarfile
from pathlib import Path

from .base import ID_RE, Item, SpoolError, check_meta


def parse_list(text: str) -> list[Item]:
    items = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 3 or not ID_RE.fullmatch(parts[0]) or not parts[1].isdigit() or parts[2] not in ("0", "1"):
            raise SpoolError(f"bad list line: {line[:80]!r}")
        items.append(Item(parts[0], int(parts[1]), parts[2] == "1"))
    return items


def extract(tar_bytes: bytes, item_id: str, dest) -> tuple[dict, Path | None]:
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True, mode=0o700)
    allowed = {f"{item_id}.json", f"{item_id}.wav"}
    meta, wav = None, None
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as tar:
        for member in tar.getmembers():
            if member.name not in allowed or not member.isfile():
                raise SpoolError(f"unexpected tar member {member.name[:80]!r}")
            data = tar.extractfile(member).read()
            if member.name.endswith(".json"):
                meta = json.loads(data)
            else:
                wav = dest / member.name
                wav.write_bytes(data)
    if meta is None:
        raise SpoolError("tar has no metadata")
    return check_meta(meta, item_id), wav


class SshSpool:
    def __init__(self, target, key, known_hosts, *, runner=subprocess.run):
        self.runner = runner
        self.base = ["ssh", "-T", "-i", str(key), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                     "-o", f"UserKnownHostsFile={known_hosts}", "-o", "StrictHostKeyChecking=yes",
                     "-o", "ConnectTimeout=15", target]

    def _run(self, command: str) -> bytes:
        verb = command.split()[0]
        try:
            r = self.runner(self.base + [command], capture_output=True, timeout=120)
        except subprocess.TimeoutExpired:
            raise SpoolError(f"{verb}: timeout") from None
        except OSError as e:
            raise SpoolError(f"{verb}: {type(e).__name__}: {e}") from None
        if r.returncode != 0:
            raise SpoolError(f"{verb}: exit {r.returncode}: {r.stderr[:200]!r}")
        return r.stdout

    def list(self) -> list[Item]:
        return parse_list(self._run("list").decode())

    def get(self, item_id, dest):
        if not ID_RE.fullmatch(item_id):
            raise SpoolError("get: bad id")
        return extract(self._run(f"get {item_id}"), item_id, dest)

    def ack(self, item_id) -> None:
        if not ID_RE.fullmatch(item_id):
            raise SpoolError("ack: bad id")
        self._run(f"ack {item_id}")
