"""Spool backends: `local` (shared directory, default) and `ssh` (split mode, vm-spool)."""
from .base import ID_RE, Item, Spool, SpoolError, check_meta

__all__ = ["ID_RE", "Item", "Spool", "SpoolError", "check_meta", "build_spool"]


def build_spool(cfg) -> Spool:
    if cfg.spool.backend == "ssh":
        from .ssh import SshSpool
        return SshSpool(cfg.spool.ssh_target, cfg.spool.ssh_key, cfg.spool.known_hosts)
    from .local import LocalSpool
    return LocalSpool(cfg.spool.path)
