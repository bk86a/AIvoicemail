"""aivoicemail check: everything that can be verified before the first call.

Offline: templates, voices, transparency elements, privacy URL, email labels, environment
variables, .env mode, override WAVs, CDR retention hint. --online: provider endpoints and SMTP."""
import smtplib
import ssl
import stat
from dataclasses import dataclass
from pathlib import Path

from . import http
from . import prompts as prompts_mod
from .config import LOCAL_STT, secret
from .render import LABELS
from .tts.audio import check_format


@dataclass(frozen=True)
class Finding:
    level: str
    message: str


def _env_names(cfg):
    """(variable, required) for every environment variable the config names and would use."""
    names = []
    if cfg.mail.smtp_user_env:
        names += [(cfg.mail.smtp_user_env, True), (cfg.mail.smtp_password_env, True)]
    if cfg.tts.engine == "azure":
        names.append((cfg.tts.azure_key_env, True))
    for n in cfg.stt.chain:
        if n != LOCAL_STT and cfg.stt.endpoints[n].key_env:
            names.append((cfg.stt.endpoints[n].key_env, False))
    for n in cfg.llm.chain:
        if cfg.llm.endpoints[n].key_env:
            names.append((cfg.llm.endpoints[n].key_env, False))
    seen, out = set(), []
    for name, required in names:
        if name not in seen:
            seen.add(name)
            out.append((name, required))
    return out


def _online(cfg, env, http_get, smtp, smtp_ssl):
    found = []
    endpoints = ([cfg.stt.endpoints[n] for n in cfg.stt.chain if n != LOCAL_STT]
                 + [cfg.llm.endpoints[n] for n in cfg.llm.chain])
    for e in endpoints:
        if e.kind != "openai_compatible":
            continue
        try:
            http_get(e.base_url.rstrip("/") + "/models", headers=http.auth_headers(e.auth, secret(env, e.key_env)))
        except http.ProviderError as ex:
            if ex.status in (401, 403):
                found.append(Finding("error", f"{e.name}: {e.base_url} rejected the key (HTTP {ex.status})"))
            elif ex.status is None:
                found.append(Finding("error", f"{e.name}: {e.base_url} unreachable ({ex.reason or ex})"))
            else:
                found.append(Finding("warning", f"{e.name}: {e.base_url}/models answered HTTP {ex.status}"))
    m = cfg.mail
    user, password = secret(env, m.smtp_user_env), secret(env, m.smtp_password_env)
    try:
        if m.smtp_security == "tls":
            conn = smtp_ssl(m.smtp_host, m.smtp_port, timeout=15, context=ssl.create_default_context())
        else:
            conn = smtp(m.smtp_host, m.smtp_port, timeout=15)
        with conn:
            if m.smtp_security == "starttls":
                conn.starttls(context=ssl.create_default_context())
            if user:
                conn.login(user, password)
    except (smtplib.SMTPException, OSError) as ex:
        found.append(Finding("error", f"SMTP {m.smtp_host}:{m.smtp_port}: {type(ex).__name__}: {str(ex)[:200]}"))
    return found


def run_checks(cfg, env, *, env_file=None, online=False, http_get=http.get, smtp=smtplib.SMTP,
               smtp_ssl=smtplib.SMTP_SSL):
    found = []
    err = lambda msg: found.append(Finding("error", msg))
    warn = lambda msg: found.append(Finding("warning", msg))

    try:
        templates = prompts_mod.load_templates(cfg.paths.prompts_dir, cfg.prompt_text)
    except (prompts_mod.TemplateError, OSError) as e:
        err(f"prompt templates: {e}")
        templates = {}
    for line in cfg.lines:
        if not line.privacy_url.strip():
            err(f"line {line.id}: privacy URL is empty")
        if line.email_language not in LABELS:
            warn(f"line {line.id}: no email labels for {line.email_language!r}; English is used")
    if cfg.tts.engine != "placeholder":
        for lang in sorted({l for line in cfg.lines for l in line.menu}):
            if lang not in cfg.tts.voices:
                err(f"[tts].voices: no voice for menu language {lang!r}")
    for problem in prompts_mod.transparency_problems(cfg, templates):
        err(problem)

    for name, required in _env_names(cfg):
        if not secret(env, name):
            (err if required else warn)(f"environment variable {name} is not set")
    usable = [n for n in cfg.llm.chain
              if cfg.llm.endpoints[n].key_env is None or secret(env, cfg.llm.endpoints[n].key_env)]
    if not usable:
        err("[llm]: no provider in the chain has its key set; every message would arrive as a fallback email")
    if env_file is not None and Path(env_file).is_file() and Path(env_file).stat().st_mode & stat.S_IROTH:
        warn(f"{env_file} is world-readable; run: chmod 600 {env_file}")

    names = set(prompts_mod.prompt_names(cfg))
    overrides = Path(cfg.paths.overrides_dir)
    if overrides.is_dir():
        for path in sorted(overrides.glob("*.wav")):
            if path.stem not in names:
                warn(f"{path}: not a prompt of this config (see generated/prompts.txt)")
            else:
                problem = check_format(path)
                if problem:
                    err(f"{path}: {problem}")
    if cfg.retention.cdr and cfg.retention.call_log_days != 90:
        warn("[retention]: CDR is on and call_log_days is not 90 - set rotate/maxage in "
             "deploy/logrotate/aivoicemail-cdr to the same number of days")

    if online:
        found += _online(cfg, env, http_get, smtp, smtp_ssl)
    return found
