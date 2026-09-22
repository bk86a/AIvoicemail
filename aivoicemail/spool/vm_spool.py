#!/usr/bin/env python3
"""vm-spool: SSH forced command giving the worker `list`, `get <uuid>`, `ack <uuid>` on the spool.

Install on the telephony host as /usr/local/bin/vm-spool and pin the worker's key in
~aivm-spool/.ssh/authorized_keys:
    restrict,from="<worker IP>",command="/usr/local/bin/vm-spool" ssh-ed25519 AAAA...
Stdlib only and free of package imports so it can be copied to a host without the package.
"""
import os
import re
import stat
import sys
import tarfile
from pathlib import Path

ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def ready_dir() -> Path:
    return Path(os.environ.get("VM_SPOOL", "/srv/aivoicemail/spool")) / "ready"


def list_ready(ready: Path) -> list[tuple[int, str, bool]]:
    """(mtime, id, has_audio) per <uuid>.json regular file, oldest first.

    Raises OSError when the directory is missing or unreadable (reported as an unreachable spool)."""
    with os.scandir(ready) as it:
        entries = {e.name: e for e in it}
    items = []
    for name, entry in entries.items():
        ident = name[:-5] if name.endswith(".json") else ""
        if not ID_RE.fullmatch(ident) or not entry.is_file(follow_symlinks=False):
            continue
        wav = entries.get(f"{ident}.wav")
        has_audio = wav is not None and wav.is_file(follow_symlinks=False)
        items.append((int(entry.stat(follow_symlinks=False).st_mtime), ident, has_audio))
    return sorted(items)


def _regular(path: Path) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except FileNotFoundError:
        return False


def cmd_list() -> None:
    try:
        items = list_ready(ready_dir())
    except OSError:
        print("spool unavailable", file=sys.stderr)
        sys.exit(4)
    for mtime, ident, has_audio in items:
        print(f"{ident} {mtime} {int(has_audio)}")


def cmd_get(ident: str) -> None:
    ready = ready_dir()
    meta = ready / f"{ident}.json"
    if not _regular(meta):
        sys.exit(3)
    with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as tar:
        tar.add(meta, arcname=meta.name)
        wav = ready / f"{ident}.wav"
        if _regular(wav):
            tar.add(wav, arcname=wav.name)


def cmd_ack(ident: str) -> None:
    ready = ready_dir()
    for suffix in (".wav", ".json"):
        (ready / f"{ident}{suffix}").unlink(missing_ok=True)


def main() -> None:
    raw = os.environ.get("SSH_ORIGINAL_COMMAND")
    words = raw.split(" ") if raw is not None else sys.argv[1:]
    if words == ["list"]:
        cmd_list()
    elif len(words) == 2 and words[0] in ("get", "ack") and ID_RE.fullmatch(words[1]):
        (cmd_get if words[0] == "get" else cmd_ack)(words[1])
    else:
        print("rejected", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
