"""Load and validate the single TOML config file.

Secrets never live in the config: it only names environment variables (`*_env` keys), resolved
from the process environment or a `.env` file. Every structural problem is collected and raised
together as one ConfigError so `aivoicemail check` can report them all at once.
"""
from __future__ import annotations

import ipaddress
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

LINE_ID_RE = re.compile(r"[a-z][a-z0-9]{0,15}")
LANG_RE = re.compile(r"[a-z]{2}")
DID_RE = re.compile(r"[0-9]{6,15}")
ENV_NAME_RE = re.compile(r"[A-Z_][A-Z0-9_]*")
# Charset allow-list for any IP/CIDR value, checked before handing the string to `ipaddress`.
# `ipaddress` accepts IPv6 scope ids ("fe80::1%eth0") with very little restriction on the scope
# text (including newlines and brackets), which would otherwise let a config value inject
# arbitrary lines into the generated Asterisk/nftables files. No '%' is allowed here, so scope
# ids are rejected outright rather than sanitised.
IP_CHARS_RE = re.compile(r"[0-9A-Fa-f.:/]+")
# `bind`'s host must round-trip through generate.py's own charset checks (its _BIND_RE): an
# unbracketed host is IPv4-only syntax (hex digits and dots, no colon) and a bracketed host is
# IPv6-only syntax (hex digits and colons, no dot) - so a bracketed IPv4 address, an IPv4-mapped
# IPv6 literal with embedded dots, or an unbracketed (ambiguous) IPv6 host must all be rejected
# here even though `ipaddress` alone would accept them.
_BIND_HOST_V4_RE = re.compile(r"[0-9A-Fa-f.]+")
_BIND_HOST_V6_RE = re.compile(r"[0-9A-Fa-f:]+")
LOCAL_STT = "whisper_local"
STRUCTURED = ("json_schema", "json_object", "none")
AUTH = ("bearer", "api-key")
ENDPOINT_KINDS = ("openai_compatible", "anthropic")
SMTP_SECURITY = ("starttls", "tls", "none")
TTS_ENGINES = ("piper", "azure", "placeholder")
SPOOL_BACKENDS = ("local", "ssh")
PROMPT_KEYS = ("menu_option", "notice", "notice_short", "after_tone", "thanks", "thanks_short")
MAX_MENU = 9
TOP_LEVEL = ("company", "trunk", "line", "recording", "stt", "llm", "mail", "tts", "retention",
             "worker", "spool", "firewall", "paths", "prompt_text")


class ConfigError(Exception):
    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("\n".join(self.problems))


@dataclass(frozen=True)
class Company:
    name: str
    privacy_url_default: str


@dataclass(frozen=True)
class Trunk:
    provider: str
    signalling_ranges: tuple[str, ...]
    media_ranges: tuple[str, ...]
    public_ip: str | None = None
    local_net: str | None = None
    bind: str = "0.0.0.0:5060"
    media_address: str | None = None
    allow_local_test: bool = True

    @property
    def sip_port(self) -> int:
        return int(self.bind.rsplit(":", 1)[1])


@dataclass(frozen=True)
class Line:
    id: str
    did: str
    mailbox: str
    email_language: str
    menu: tuple[str, ...]
    default_language: str
    privacy_url: str

    @property
    def has_menu(self) -> bool:
        return len(self.menu) > 1


@dataclass(frozen=True)
class Recording:
    max_seconds: int = 180
    silence_seconds: int = 5
    min_speech_seconds: float = 2.0


@dataclass(frozen=True)
class Endpoint:
    name: str
    model: str
    base_url: str = ""
    key_env: str | None = None
    structured: str = "json_schema"
    auth: str = "bearer"
    kind: str = "openai_compatible"


@dataclass(frozen=True)
class Stt:
    chain: tuple[str, ...]
    whisper_model: str = "large-v3"
    whisper_threads: int = 4
    endpoints: Mapping[str, Endpoint] = field(default_factory=dict)


@dataclass(frozen=True)
class Llm:
    chain: tuple[str, ...]
    endpoints: Mapping[str, Endpoint] = field(default_factory=dict)


@dataclass(frozen=True)
class Mail:
    smtp_host: str
    smtp_port: int
    from_address: str
    from_name: str
    alert_to: str
    smtp_security: str = "starttls"
    smtp_user_env: str | None = None
    smtp_password_env: str | None = None


@dataclass(frozen=True)
class Tts:
    engine: str = "piper"
    voices: Mapping[str, str] = field(default_factory=dict)
    pronunciation: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    sentence_ms: int = 300
    azure_region: str | None = None
    azure_key_env: str | None = None


@dataclass(frozen=True)
class Retention:
    call_log_days: int = 90
    cdr: bool = False


@dataclass(frozen=True)
class Worker:
    poll_seconds: int = 30
    stale_minutes: int = 60
    unreachable_minutes: int = 15
    max_failures: int = 5


@dataclass(frozen=True)
class SpoolCfg:
    backend: str = "local"
    path: Path = Path("/srv/aivoicemail/spool")
    ssh_target: str | None = None
    ssh_key: Path | None = None
    known_hosts: Path | None = None


@dataclass(frozen=True)
class Firewall:
    allow_tcp: tuple[int, ...] = (22,)
    allow_udp: tuple[int, ...] = ()


@dataclass(frozen=True)
class Paths:
    root: Path
    data_dir: Path
    work_dir: Path
    prompts_dir: Path
    overrides_dir: Path
    generated_dir: Path


@dataclass(frozen=True)
class Config:
    company: Company
    trunk: Trunk
    lines: tuple[Line, ...]
    recording: Recording
    stt: Stt
    llm: Llm
    mail: Mail
    tts: Tts
    retention: Retention
    worker: Worker
    spool: SpoolCfg
    firewall: Firewall
    paths: Paths
    prompt_text: Mapping[str, Mapping[str, str]]
    source: Path

    def line(self, line_id) -> Line | None:
        return next((l for l in self.lines if l.id == line_id), None)

    def line_by_did(self, did) -> Line | None:
        return next((l for l in self.lines if l.did == did), None)


_MISSING = object()


class _Reader:
    """Typed accessors that record problems instead of raising, so all are reported together."""

    def __init__(self):
        self.problems: list[str] = []

    def err(self, msg: str) -> None:
        self.problems.append(msg)

    def section(self, raw: Mapping, key: str, *, required: bool = True) -> dict:
        value = raw.get(key, _MISSING)
        if value is _MISSING:
            if required:
                self.err(f"[{key}]: required section missing")
            return {}
        if not isinstance(value, dict):
            self.err(f"[{key}]: expected a table")
            return {}
        return value

    def get(self, table: Mapping, key: str, where: str, kind: type, default: Any = _MISSING) -> Any:
        if key not in table:
            if default is _MISSING:
                self.err(f"{where}.{key}: required")
                return None
            return default
        value = table[key]
        if kind is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if (kind in (int, float) and isinstance(value, bool)) or not isinstance(value, kind):
            self.err(f"{where}.{key}: expected {kind.__name__}, got {type(value).__name__}")
            return None if default is _MISSING else default
        return value

    def strings(self, table: Mapping, key: str, where: str, default: Any = _MISSING) -> tuple[str, ...]:
        value = self.get(table, key, where, list, default)
        if value is None:
            return ()
        if not all(isinstance(v, str) for v in value):
            self.err(f"{where}.{key}: expected a list of strings")
            return ()
        return tuple(value)

    def ports(self, table: Mapping, key: str, where: str, default: tuple[int, ...]) -> tuple[int, ...]:
        value = self.get(table, key, where, list, list(default))
        if value is None or not all(isinstance(v, int) and not isinstance(v, bool) and 0 < v < 65536 for v in value):
            self.err(f"{where}.{key}: expected a list of port numbers")
            return default
        return tuple(value)

    def known(self, table: Mapping, allowed: tuple[str, ...], where: str) -> None:
        for key in table:
            if key not in allowed:
                self.err(f"{where}: unknown key {key!r}")

    def env_name(self, value: str | None, where: str) -> str | None:
        if value is not None and not ENV_NAME_RE.fullmatch(value):
            self.err(f"{where}: {value!r} is not an environment variable name (A-Z, 0-9, _)")
        return value

    def cidr(self, value: str, where: str) -> None:
        if not IP_CHARS_RE.fullmatch(value):
            self.err(f"{where}: {value!r} is not a valid CIDR (invalid characters)")
            return
        try:
            ipaddress.ip_network(value, strict=True)
        except ValueError as e:
            self.err(f"{where}: {value!r} is not a valid CIDR ({e})")

    def ip(self, value: str | None, where: str) -> str | None:
        if value:
            if not IP_CHARS_RE.fullmatch(value):
                self.err(f"{where}: {value!r} is not an IP address (invalid characters)")
                return value
            try:
                ipaddress.ip_address(value)
            except ValueError:
                self.err(f"{where}: {value!r} is not an IP address")
        return value or None

    def positive(self, table: Mapping, key: str, where: str, kind: type, default: Any) -> Any:
        value = self.get(table, key, where, kind, default)
        if value is not None and value <= 0:
            self.err(f"{where}.{key}: must be positive")
            return default
        return value


def _split_bind(bind: str) -> tuple[str | None, str]:
    """Split "host:port" or the bracketed IPv6 form "[host]:port" into (host, port)."""
    if bind.startswith("["):
        end = bind.find("]")
        if end == -1 or bind[end + 1:end + 2] != ":":
            return None, ""
        return bind[1:end], bind[end + 2:]
    host, sep, port = bind.rpartition(":")
    if not sep:
        return None, ""
    return host, port


def _bind_host_ok(bracketed: bool, host: str) -> bool:
    """host must match the charset generate.py's bind regex accepts for this form (bracketed =
    IPv6-only syntax, unbracketed = IPv4-only syntax) and be a real address of that family."""
    try:
        if bracketed:
            return bool(_BIND_HOST_V6_RE.fullmatch(host)) and bool(ipaddress.IPv6Address(host))
        return bool(_BIND_HOST_V4_RE.fullmatch(host)) and bool(ipaddress.IPv4Address(host))
    except ValueError:
        return False


def _path(root: Path, value: str | Path) -> Path:
    return root / Path(value)  # an absolute value replaces root


def _company(r: _Reader, t: dict) -> Company:
    w = "[company]"
    r.known(t, ("name", "privacy_url_default"), w)
    return Company(r.get(t, "name", w, str) or "", r.get(t, "privacy_url_default", w, str) or "")


def _trunk(r: _Reader, t: dict) -> Trunk:
    w = "[trunk]"
    r.known(t, ("provider", "signalling_ranges", "media_ranges", "public_ip", "local_net", "bind",
                "media_address", "allow_local_test"), w)
    sig = r.strings(t, "signalling_ranges", w)
    if "signalling_ranges" in t and not sig:
        r.err(f"{w}.signalling_ranges: at least one range is required")
    med = r.strings(t, "media_ranges", w, [])
    for c in sig:
        r.cidr(c, f"{w}.signalling_ranges")
    for c in med:
        r.cidr(c, f"{w}.media_ranges")
    local_net = r.get(t, "local_net", w, str, None)
    if local_net:
        r.cidr(local_net, f"{w}.local_net")
    bind = r.get(t, "bind", w, str, "0.0.0.0:5060")
    host, port = _split_bind(bind)
    if (not host or not port.isascii() or not port.isdigit() or not 0 < int(port) < 65536
            or not _bind_host_ok(bind.startswith("["), host)):
        r.err(f"{w}.bind: {bind!r} must be host:port or [ipv6-host]:port")
        bind = "0.0.0.0:5060"
    return Trunk(
        provider=r.get(t, "provider", w, str, "generic"),
        signalling_ranges=sig, media_ranges=med or sig,
        public_ip=r.ip(r.get(t, "public_ip", w, str, None), f"{w}.public_ip"),
        local_net=local_net or None, bind=bind,
        media_address=r.ip(r.get(t, "media_address", w, str, None), f"{w}.media_address"),
        allow_local_test=r.get(t, "allow_local_test", w, bool, True),
    )


def _lines(r: _Reader, raw_lines: Any, company: Company) -> tuple[Line, ...]:
    if not isinstance(raw_lines, list) or not raw_lines:
        r.err("[[line]]: at least one line is required")
        return ()
    out = []
    for i, t in enumerate(raw_lines, start=1):
        w = f"[[line]] #{i}"
        if not isinstance(t, dict):
            r.err(f"{w}: expected a table")
            continue
        r.known(t, ("id", "did", "mailbox", "email_language", "menu", "default_language", "privacy_url"), w)
        lid = r.get(t, "id", w, str) or ""
        if lid and not LINE_ID_RE.fullmatch(lid):
            r.err(f"{w}.id: {lid!r} must be a lowercase letter followed by up to 15 lowercase letters or digits")
        did = r.get(t, "did", w, str) or ""
        if did and not DID_RE.fullmatch(did):
            r.err(f"{w}.did: {did!r} must be 6-15 digits only (E.164 without +)")
        mailbox = r.get(t, "mailbox", w, str) or ""
        if mailbox and "@" not in mailbox:
            r.err(f"{w}.mailbox: {mailbox!r} is not an e-mail address")
        email_language = r.get(t, "email_language", w, str, "en")
        if not LANG_RE.fullmatch(email_language):
            r.err(f"{w}.email_language: {email_language!r} must be a two-letter code")
        menu = r.strings(t, "menu", w)
        if "menu" in t and not menu:
            r.err(f"{w}.menu: at least one language is required")
        if len(menu) > MAX_MENU:
            r.err(f"{w}.menu: at most {MAX_MENU} languages (keys 1-9)")
        if len(set(menu)) != len(menu):
            r.err(f"{w}.menu: duplicate language")
        for lang in menu:
            if not LANG_RE.fullmatch(lang):
                r.err(f"{w}.menu: {lang!r} must be a two-letter code")
        default = r.get(t, "default_language", w, str, "auto")
        if len(menu) > 1 and default != "auto" and default not in menu:
            r.err(f"{w}.default_language: {default!r} must be \"auto\" or one of the menu languages")
        if len(menu) == 1:
            default = menu[0]
        privacy = r.get(t, "privacy_url", w, str, company.privacy_url_default)
        out.append(Line(lid, did, mailbox, email_language, menu, default, privacy))
    for attr in ("id", "did"):
        values = [getattr(l, attr) for l in out]
        for dup in sorted({v for v in values if v and values.count(v) > 1}):
            r.err(f"[[line]]: duplicate {attr} {dup!r}")
    return tuple(out)


def _endpoints(r: _Reader, t: dict, w: str, reserved: tuple[str, ...], *, llm: bool) -> dict[str, Endpoint]:
    eps = {}
    for name, value in t.items():
        if name in reserved:
            continue
        if not isinstance(value, dict):
            r.err(f"{w}: unknown key {name!r}")
            continue
        ew = f"{w}.{name}"
        r.known(value, ("kind", "base_url", "model", "key_env", "structured", "auth"), ew)
        kind = r.get(value, "kind", ew, str, "openai_compatible")
        if kind not in ENDPOINT_KINDS or (kind == "anthropic" and not llm):
            r.err(f"{ew}.kind: {kind!r} is not supported here")
        base_url = r.get(value, "base_url", ew, str, "" if kind == "anthropic" else _MISSING) or ""
        if kind == "openai_compatible" and base_url and not base_url.startswith(("https://", "http://")):
            r.err(f"{ew}.base_url: must start with https:// or http://")
        structured = r.get(value, "structured", ew, str, "json_schema")
        if structured not in STRUCTURED:
            r.err(f"{ew}.structured: {structured!r} must be one of {', '.join(STRUCTURED)}")
        auth = r.get(value, "auth", ew, str, "bearer")
        if auth not in AUTH:
            r.err(f"{ew}.auth: {auth!r} must be one of {', '.join(AUTH)}")
        eps[name] = Endpoint(
            name=name, model=r.get(value, "model", ew, str) or "", base_url=base_url,
            key_env=r.env_name(r.get(value, "key_env", ew, str, None), f"{ew}.key_env"),
            structured=structured, auth=auth, kind=kind)
    return eps


def _chain(r: _Reader, chain: tuple[str, ...], allowed: set[str], w: str) -> tuple[str, ...]:
    if not chain:
        r.err(f"{w}.chain: at least one provider is required")
    if len(set(chain)) != len(chain):
        r.err(f"{w}.chain: duplicate provider")
    for name in chain:
        if name not in allowed:
            r.err(f"{w}.chain: {name!r} is not defined in {w}")
    return chain


def _stt(r: _Reader, t: dict) -> Stt:
    w = "[stt]"
    reserved = ("chain", "whisper_model", "whisper_threads")
    eps = _endpoints(r, t, w, reserved, llm=False)
    chain = _chain(r, r.strings(t, "chain", w, [LOCAL_STT]), {LOCAL_STT, *eps}, w)
    return Stt(chain=chain, whisper_model=r.get(t, "whisper_model", w, str, "large-v3"),
               whisper_threads=r.positive(t, "whisper_threads", w, int, 4), endpoints=eps)


def _llm(r: _Reader, t: dict) -> Llm:
    w = "[llm]"
    eps = _endpoints(r, t, w, ("chain",), llm=True)
    return Llm(chain=_chain(r, r.strings(t, "chain", w), set(eps), w), endpoints=eps)


def _mail(r: _Reader, t: dict) -> Mail:
    w = "[mail]"
    r.known(t, ("smtp_host", "smtp_port", "smtp_security", "smtp_user_env", "smtp_password_env", "from",
                "from_name", "alert_to"), w)
    port = r.positive(t, "smtp_port", w, int, 587)
    security = r.get(t, "smtp_security", w, str, "tls" if port == 465 else "starttls")
    if security not in SMTP_SECURITY:
        r.err(f"{w}.smtp_security: {security!r} must be one of {', '.join(SMTP_SECURITY)}")
    user_env = r.env_name(r.get(t, "smtp_user_env", w, str, None), f"{w}.smtp_user_env")
    password_env = r.env_name(r.get(t, "smtp_password_env", w, str, None), f"{w}.smtp_password_env")
    if (user_env is None) != (password_env is None):
        r.err(f"{w}: smtp_user_env and smtp_password_env must be set together")
    addresses = {k: r.get(t, k, w, str) or "" for k in ("from", "alert_to")}
    for k, v in addresses.items():
        if v and "@" not in v:
            r.err(f"{w}.{k}: {v!r} is not an e-mail address")
    return Mail(smtp_host=r.get(t, "smtp_host", w, str) or "", smtp_port=port,
                from_address=addresses["from"], from_name=r.get(t, "from_name", w, str, "Voicemail"),
                alert_to=addresses["alert_to"], smtp_security=security,
                smtp_user_env=user_env, smtp_password_env=password_env)


def _tts(r: _Reader, t: dict) -> Tts:
    w = "[tts]"
    r.known(t, ("engine", "voices", "pronunciation", "sentence_ms", "azure"), w)
    engine = r.get(t, "engine", w, str, "piper")
    if engine not in TTS_ENGINES:
        r.err(f"{w}.engine: {engine!r} must be one of {', '.join(TTS_ENGINES)}")
    voices = r.get(t, "voices", w, dict, {})
    if not all(isinstance(k, str) and LANG_RE.fullmatch(k) and isinstance(v, str) for k, v in voices.items()):
        r.err(f"{w}.voices: expected two-letter language = \"voice name\" pairs")
    pron = r.get(t, "pronunciation", w, dict, {})
    for word, per_lang in pron.items():
        if not isinstance(per_lang, dict) or not all(isinstance(v, str) for v in per_lang.values()):
            r.err(f"{w}.pronunciation.{word}: expected language = \"IPA\" pairs")
    azure = r.get(t, "azure", w, dict, {})
    r.known(azure, ("region", "key_env"), f"{w}.azure")
    if engine == "azure" and not (azure.get("region") and azure.get("key_env")):
        r.err(f"{w}.azure: region and key_env are required for engine = \"azure\"")
    return Tts(engine=engine, voices=dict(voices), pronunciation={k: dict(v) for k, v in pron.items() if isinstance(v, dict)},
               sentence_ms=r.positive(t, "sentence_ms", w, int, 300), azure_region=azure.get("region"),
               azure_key_env=r.env_name(azure.get("key_env"), f"{w}.azure.key_env"))


def _spool(r: _Reader, t: dict, root: Path) -> SpoolCfg:
    w = "[spool]"
    r.known(t, ("backend", "path", "ssh_target", "ssh_key", "known_hosts"), w)
    backend = r.get(t, "backend", w, str, "local")
    if backend not in SPOOL_BACKENDS:
        r.err(f"{w}.backend: {backend!r} must be one of {', '.join(SPOOL_BACKENDS)}")
    if backend == "ssh":
        for key in ("ssh_target", "ssh_key", "known_hosts"):
            if not t.get(key):
                r.err(f"{w}.{key}: required when backend = \"ssh\"")
    opt = lambda key: _path(root, t[key]) if isinstance(t.get(key), str) and t[key] else None
    return SpoolCfg(backend=backend, path=_path(root, r.get(t, "path", w, str, "/srv/aivoicemail/spool")),
                    ssh_target=t.get("ssh_target") or None, ssh_key=opt("ssh_key"), known_hosts=opt("known_hosts"))


def _prompt_text(r: _Reader, t: dict) -> dict[str, dict[str, str]]:
    out = {}
    for lang, texts in t.items():
        w = f"[prompt_text.{lang}]"
        if not LANG_RE.fullmatch(lang) or not isinstance(texts, dict):
            r.err(f"{w}: expected a two-letter language table")
            continue
        r.known(texts, PROMPT_KEYS, w)
        if not all(isinstance(v, str) for v in texts.values()):
            r.err(f"{w}: values must be strings")
        out[lang] = {k: v for k, v in texts.items() if isinstance(v, str)}
    return out


def load(path) -> Config:
    path = Path(path).resolve()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError([f"{path}: {e}"]) from None
    root = path.parent.parent
    r = _Reader()
    r.known(raw, TOP_LEVEL, "config")
    company = _company(r, r.section(raw, "company"))
    trunk = _trunk(r, r.section(raw, "trunk"))
    lines = _lines(r, raw.get("line"), company)
    rec_t = r.section(raw, "recording", required=False)
    r.known(rec_t, ("max_seconds", "silence_seconds", "min_speech_seconds"), "[recording]")
    recording = Recording(r.positive(rec_t, "max_seconds", "[recording]", int, 180),
                          r.positive(rec_t, "silence_seconds", "[recording]", int, 5),
                          r.positive(rec_t, "min_speech_seconds", "[recording]", float, 2.0))
    stt = _stt(r, r.section(raw, "stt", required=False))
    llm = _llm(r, r.section(raw, "llm"))
    mail = _mail(r, r.section(raw, "mail"))
    tts = _tts(r, r.section(raw, "tts", required=False))
    ret_t = r.section(raw, "retention", required=False)
    r.known(ret_t, ("call_log_days", "cdr"), "[retention]")
    retention = Retention(r.positive(ret_t, "call_log_days", "[retention]", int, 90),
                          r.get(ret_t, "cdr", "[retention]", bool, False))
    wk_t = r.section(raw, "worker", required=False)
    r.known(wk_t, ("poll_seconds", "stale_minutes", "unreachable_minutes", "max_failures"), "[worker]")
    worker = Worker(*(r.positive(wk_t, k, "[worker]", int, d) for k, d in
                      (("poll_seconds", 30), ("stale_minutes", 60), ("unreachable_minutes", 15), ("max_failures", 5))))
    spool = _spool(r, r.section(raw, "spool", required=False), root)
    fw_t = r.section(raw, "firewall", required=False)
    r.known(fw_t, ("allow_tcp", "allow_udp"), "[firewall]")
    firewall = Firewall(r.ports(fw_t, "allow_tcp", "[firewall]", (22,)), r.ports(fw_t, "allow_udp", "[firewall]", ()))
    p_t = r.section(raw, "paths", required=False)
    defaults = {"data_dir": "/var/lib/aivoicemail", "work_dir": "/run/aivoicemail", "prompts_dir": "prompts",
                "overrides_dir": "overrides", "generated_dir": "generated"}
    r.known(p_t, tuple(defaults), "[paths]")
    paths = Paths(root=root, **{k: _path(root, r.get(p_t, k, "[paths]", str, d)) for k, d in defaults.items()})
    prompt_text = _prompt_text(r, r.section(raw, "prompt_text", required=False))
    if r.problems:
        raise ConfigError(r.problems)
    return Config(company, trunk, lines, recording, stt, llm, mail, tts, retention, worker, spool, firewall,
                  paths, prompt_text, path)


def load_env(path, environ=None) -> dict[str, str]:
    """KEY=VALUE lines from `path` (if it exists), overridden by the process environment."""
    values: dict[str, str] = {}
    if path is not None and Path(path).is_file():
        for n, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].lstrip()
            key, sep, value = line.partition("=")
            key = key.strip()
            if not sep or not ENV_NAME_RE.fullmatch(key):
                raise ConfigError([f"{path}:{n}: expected KEY=VALUE"])
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            values[key] = value
    values.update(os.environ if environ is None else environ)
    return values


def secret(env: Mapping[str, str], name: str | None) -> str | None:
    if not name:
        return None
    return (env.get(name) or "").strip() or None
