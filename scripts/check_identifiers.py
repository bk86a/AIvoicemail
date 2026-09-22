#!/usr/bin/env python3
"""Fail when the repository contains deployment-specific identifiers.

The blocklist is loaded at run time from $AIVM_BLOCKLIST or the git-ignored .identifier-blocklist;
the repository never contains it, not even hashed. Tokens are: words ([a-z0-9]+), UUIDs, IPv4 literals and digit runs of phone-like
strings with separators removed. Also rejected: public IPv4 literals outside documentation,
private and carrier ranges; e-mail addresses outside example domains; home/nas host aliases.

Usage: check_identifiers.py            scan tracked files of the work tree
       check_identifiers.py --history  also scan every blob, commit message, author/committer
                                       identity and tag message in git history
"""
import hashlib
import ipaddress
import os
import re
import subprocess
import sys
from pathlib import Path

BLOCKLIST_ENV = "AIVM_BLOCKLIST"
BLOCKLIST_FILE = ".identifier-blocklist"  # git-ignored; one token per line, "#" comments


def load_blocklist(root, env=os.environ):
    """Digests of the blocklist tokens. The tokens are never stored in the repository."""
    raw = env.get(BLOCKLIST_ENV, "")
    path = Path(root) / BLOCKLIST_FILE
    if not raw and path.is_file():
        raw = "\n".join(l for l in path.read_text().splitlines() if not l.lstrip().startswith("#"))
    tokens = {t.strip().lower() for t in re.split(r"[,\n]", raw) if t.strip()}
    return frozenset(_sha(t) for t in tokens)


ALLOWED_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "10.0.0.0/8", "127.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "255.0.0.0/8",
    "46.19.208.0/21", "185.238.172.0/22",  # DIDWW signalling/media ranges (public documentation)
))
ALLOWED_EMAIL_DOMAIN = re.compile(
    r"(?:[a-z0-9-]+\.)*(?:example|example\.com|example\.org|example\.net)"
    r"|anthropic\.com|github\.com|users\.noreply\.github\.com")
WORD = re.compile(r"[a-z0-9]+")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
IPV4 = re.compile(r"(?<![\w.=])(\d{1,3}(?:\.\d{1,3}){3})(?![\w.])")
PHONEISH = re.compile(r"\+?\d[\d ().-]{7,}\d")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
HOST_PATTERNS = (re.compile(r"\bssh\s+(?:home|nas)\b"), re.compile(r"https?://(?:home|nas)\b"))


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def violations(text, blocked):
    blocked = set(blocked)
    low = text.lower()
    out = []
    tokens = set(WORD.findall(low)) | set(UUID.findall(low)) | set(IPV4.findall(low))
    tokens |= {re.sub(r"\D", "", m) for m in PHONEISH.findall(low)}
    for token in sorted(tokens):
        digest = _sha(token)
        if digest in blocked:
            out.append(f"blocked identifier (sha256 {digest[:12]})")
    for ip in IPV4.findall(text):
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if not any(addr in net for net in ALLOWED_NETWORKS):
            out.append(f"public IPv4 literal {ip}")
    for domain in EMAIL.findall(text):
        d = domain.lower()
        if re.fullmatch(r"[\d.]+", d):
            continue  # user@IP in SIP URIs and SSH targets; the IP rule above covers it
        if not ALLOWED_EMAIL_DOMAIN.fullmatch(d):
            shown = "blocked domain" if any(_sha(w) in blocked for w in WORD.findall(d)) else d
            out.append(f"e-mail address outside example domains (@{shown})")
    for pattern in HOST_PATTERNS:
        if pattern.search(low):
            out.append(f"host alias pattern {pattern.pattern!r}")
    return out


def _is_text(data: bytes) -> bool:
    return b"\0" not in data


def scan_worktree(root, blocked=frozenset()):
    root = Path(root)
    names = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True).stdout
    found = []
    for name in filter(None, names.decode().split("\0")):
        path = root / name
        if not path.is_file():
            continue
        data = path.read_bytes()
        if _is_text(data):
            found += [f"{name}: {v}" for v in violations(data.decode("utf-8", "replace"), blocked)]
    return found


def scan_history(root, blocked=frozenset()):
    root = Path(root)
    git = lambda *a: subprocess.run(["git", *a], cwd=root, capture_output=True, check=True).stdout
    text = lambda *a: git(*a).decode("utf-8", "replace")
    found = [f"commit messages: {v}" for v in violations(text("log", "--all", "--format=%B"), blocked)]
    found += [f"commit metadata: {v}" for v in violations(text("log", "--all", "--format=%an%n%ae%n%cn%n%ce"), blocked)]
    found += [f"tags: {v}" for v in violations(text("for-each-ref", "--format=%(contents)", "refs/tags"), blocked)]
    for line in git("rev-list", "--all", "--objects").decode().splitlines():
        sha = line.split(" ", 1)[0]
        if git("cat-file", "-t", sha).strip() != b"blob":
            continue
        data = git("cat-file", "blob", sha)
        if _is_text(data):
            found += [f"blob {sha[:12]}: {v}" for v in violations(data.decode("utf-8", "replace"), blocked)]
    return found


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    root = Path(__file__).resolve().parents[1]
    blocked = load_blocklist(root)
    if not blocked:
        print(f"notice: no blocklist ({BLOCKLIST_ENV} or {BLOCKLIST_FILE}); generic checks only")
    found = scan_worktree(root, blocked) + (scan_history(root, blocked) if "--history" in argv else [])
    for item in found:
        print(item)
    print(f"identifier check: {len(found)} problem(s)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
