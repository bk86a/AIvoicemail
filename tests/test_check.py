import dataclasses
import os
import smtplib

from aivoicemail import cli, http
from aivoicemail.check import Finding, run_checks
from conftest import ENV, EXAMPLE, write_wav


def env_file(tmp_path, mode=0o600):
    p = tmp_path / ".env"
    p.write_text("".join(f"{k}={v}\n" for k, v in ENV.items()))
    os.chmod(p, mode)
    return p


def errors(findings):
    return [f.message for f in findings if f.level == "error"]


def warnings(findings):
    return [f.message for f in findings if f.level == "warning"]


def test_example_config_is_clean(cfg, tmp_path):
    assert run_checks(cfg, ENV, env_file=env_file(tmp_path)) == []


def test_missing_smtp_credentials_are_errors(cfg):
    env = {k: v for k, v in ENV.items() if not k.startswith("SMTP")}
    assert "environment variable SMTP_USER is not set" in errors(run_checks(cfg, env))


def test_missing_provider_keys(cfg):
    env = {k: v for k, v in ENV.items() if k not in ("LLM_API_KEY", "STT_API_KEY")}
    found = run_checks(cfg, env)
    assert any("no provider in the chain has its key set" in m for m in errors(found))
    assert "environment variable STT_API_KEY is not set" in warnings(found)


def test_world_readable_env_file_warns(cfg, tmp_path):
    p = env_file(tmp_path, 0o644)
    assert any("world-readable" in m for m in warnings(run_checks(cfg, ENV, env_file=p)))


def test_missing_voice_is_error(cfg):
    tts = dataclasses.replace(cfg.tts, voices={k: v for k, v in cfg.tts.voices.items() if k != "fr"})
    assert "[tts].voices: no voice for menu language 'fr'" in errors(run_checks(dataclasses.replace(cfg, tts=tts), ENV))


def test_placeholder_engine_needs_no_voices(cfg):
    tts = dataclasses.replace(cfg.tts, engine="placeholder", voices={})
    assert run_checks(dataclasses.replace(cfg, tts=tts), ENV) == []


def test_transparency_violation_is_error(cfg):
    broken = dataclasses.replace(cfg, prompt_text={"nl": {"notice_short": "Hallo {privacy_url}."}})
    assert any("nl notice_short lacks transparency element" in m for m in errors(run_checks(broken, ENV)))


def test_empty_privacy_url_is_error(cfg):
    line = dataclasses.replace(cfg.lines[1], privacy_url=" ")
    bad = dataclasses.replace(cfg, lines=(cfg.lines[0], line))
    assert "line pl: privacy URL is empty" in errors(run_checks(bad, ENV))


def test_overrides_checked(cfg):
    write_wav(cfg.paths.overrides_dir / "be-menu.wav", 1, rate=16000)
    write_wav(cfg.paths.overrides_dir / "be-welcome.wav", 1)
    found = run_checks(cfg, ENV)
    assert any("be-menu.wav" in m and "8 kHz mono 16-bit" in m for m in errors(found))
    assert any("be-welcome.wav: not a prompt of this config" in m for m in warnings(found))


def test_unknown_email_language_warns(cfg):
    line = dataclasses.replace(cfg.lines[0], email_language="it")
    found = run_checks(dataclasses.replace(cfg, lines=(line, cfg.lines[1])), ENV)
    assert "line be: no email labels for 'it'; English is used" in warnings(found)


class OkSMTP:
    def __init__(self, host, port, timeout, context=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def starttls(self, context):
        pass

    def login(self, user, password):
        pass


def test_online_reports_rejected_key_and_unreachable(cfg):
    def http_get(url, *, headers, timeout=15):
        if "mistral" in url and headers.get("Authorization") == "Bearer stt-key":
            raise http.ProviderError("HTTP 401", 401)
        raise http.ProviderError("network error: refused")

    found = run_checks(cfg, ENV, online=True, http_get=http_get, smtp=OkSMTP)
    msgs = errors(found)
    assert any(m.startswith("remote: https://api.mistral.ai/v1 rejected the key (HTTP 401)") for m in msgs)
    assert any(m.startswith("primary: https://api.mistral.ai/v1 unreachable") for m in msgs)
    assert not any(m.startswith("SMTP") for m in msgs)


def test_online_smtp_failure(cfg):
    class Bad(OkSMTP):
        def login(self, user, password):
            raise smtplib.SMTPAuthenticationError(535, b"bad")

    found = run_checks(cfg, ENV, online=True, http_get=lambda url, headers, timeout=15: (200, b"{}"), smtp=Bad)
    assert any(m.startswith("SMTP smtp.example:587: SMTPAuthenticationError") for m in errors(found))


def test_cli_check_exit_codes(tmp_path, capsys):
    install = tmp_path / "install"
    (install / "config").mkdir(parents=True)
    (install / "prompts").symlink_to(EXAMPLE.parents[1] / "prompts")
    conf = install / "config" / "aivoicemail.toml"
    conf.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    p = env_file(install)
    assert cli.main(["--config", str(conf), "--env-file", str(p), "check"]) == 0
    assert "check: 0 error(s), 0 warning(s)" in capsys.readouterr().out
    os.chmod(p, 0o600)
    p.write_text("LLM_API_KEY=x\n")
    assert cli.main(["--config", str(conf), "--env-file", str(p), "check"]) == 1
    assert "ERROR: environment variable SMTP_USER is not set" in capsys.readouterr().out
