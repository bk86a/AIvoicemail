"""Spool interface shared by the local and SSH backends."""
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class SpoolError(Exception):
    """Transport problem (directory, SSH): retried next cycle, never counted as a poison item."""


@dataclass(frozen=True)
class Item:
    id: str
    mtime: int
    has_audio: bool


class Spool(Protocol):
    def list(self) -> list[Item]: ...

    def get(self, item_id: str, dest: Path) -> tuple[dict, Path | None]: ...

    def ack(self, item_id: str) -> None: ...

    def orphans(self) -> int: ...


def check_meta(meta, item_id: str) -> dict:
    """Content problems raise ValueError so a broken item ends in the poison-item path."""
    if not isinstance(meta, dict) or meta.get("id") != item_id:
        raise ValueError("metadata id does not match item id")
    return meta
