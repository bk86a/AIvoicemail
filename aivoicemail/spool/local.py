"""Local spool backend: the Asterisk container and the worker share one directory."""
import errno
import json
import os
import stat
from pathlib import Path

from .base import ID_RE, Item, SpoolError, check_meta
from .vm_spool import count_orphans, list_ready


def _read_regular(path: Path) -> bytes | None:
    """File content, or None when absent. A symlink or non-regular file is a content problem."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise ValueError(f"{path.name}: symlink in spool") from None
        raise SpoolError(f"{path.name}: {type(e).__name__}") from None
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise ValueError(f"{path.name}: not a regular file")
        return f.read()


class LocalSpool:
    def __init__(self, root):
        self.root = Path(root)
        self.ready = self.root / "ready"

    def list(self) -> list[Item]:
        try:
            return [Item(ident, mtime, has_audio) for mtime, ident, has_audio in list_ready(self.ready)]
        except OSError as e:
            raise SpoolError(f"list: {type(e).__name__}") from None

    def orphans(self) -> int:
        try:
            return count_orphans(self.root)
        except OSError as e:
            raise SpoolError(f"orphans: {type(e).__name__}") from None

    def get(self, item_id, dest):
        if not ID_RE.fullmatch(item_id):
            raise SpoolError("get: bad id")
        raw = _read_regular(self.ready / f"{item_id}.json")
        if raw is None:
            raise SpoolError("get: item vanished")
        meta = check_meta(json.loads(raw), item_id)
        data = _read_regular(self.ready / f"{item_id}.wav")
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True, mode=0o700)
        wav = None
        if data is not None:
            wav = dest / f"{item_id}.wav"
            wav.write_bytes(data)
        return meta, wav

    def ack(self, item_id) -> None:
        if not ID_RE.fullmatch(item_id):
            raise SpoolError("ack: bad id")
        try:
            for suffix in (".wav", ".json"):
                (self.ready / f"{item_id}{suffix}").unlink(missing_ok=True)
        except OSError as e:
            raise SpoolError(f"ack: {type(e).__name__}") from None
