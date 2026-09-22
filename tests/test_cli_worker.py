import json

from aivoicemail import cli
from aivoicemail.calllog import read_entries
from conftest import EXAMPLE, write_wav

ID = "0f8fad5b-d9cb-469f-a165-70867728950e"


def install(tmp_path):
    cfg = tmp_path / "install" / "config" / "aivoicemail.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(EXAMPLE.read_text(encoding="utf-8") +
                   f'\n[spool]\npath = "{tmp_path / "spool"}"\n'
                   f'\n[paths]\ndata_dir = "{tmp_path / "data"}"\nwork_dir = "{tmp_path / "work"}"\n', encoding="utf-8")
    return cfg


def test_worker_once_fake_providers(tmp_path, capsys):
    cfg = install(tmp_path)
    ready = tmp_path / "spool" / "ready"
    write_wav(ready / f"{ID}.wav", 4, tone_hz=440)
    (ready / f"{ID}.json").write_text(json.dumps({
        "id": ID, "line": "pl", "did": "48320000001", "caller": "withheld", "lang_choice": "pl",
        "started_at": "2026-09-22T10:00:00Z", "ended_at": "2026-09-22T10:00:04Z", "duration_s": 4, "has_audio": True}))
    assert cli.main(["--config", str(cfg), "worker", "--once", "--fake-providers"]) == 0
    [entry] = read_entries(tmp_path / "data" / "calllog")
    assert entry["outcome"] == "message" and entry["transcribed_by"] == "fake" and entry["summarised_by"] == "fake"
    out = capsys.readouterr().out
    assert f"{ID} pl message msg=<" in out and "withheld" not in out


def test_fake_mode_from_environment(tmp_path, monkeypatch):
    cfg = install(tmp_path)
    (tmp_path / "spool" / "ready").mkdir(parents=True)
    monkeypatch.setenv("AIVOICEMAIL_FAKE_PROVIDERS", "1")
    assert cli.main(["--config", str(cfg), "worker", "--once"]) == 0
    assert (tmp_path / "data" / "outbox").is_dir()


def test_config_error_exit_code(tmp_path, capsys):
    bad = tmp_path / "c" / "config" / "aivoicemail.toml"
    bad.parent.mkdir(parents=True)
    bad.write_text("[company]\nname = 1\n")
    assert cli.main(["--config", str(bad), "worker", "--once"]) == 2
    assert "ERROR: [company].name: expected str" in capsys.readouterr().err


def test_env_file_permission_error_exit_code(tmp_path, capsys):
    cfg = install(tmp_path)
    env_file = tmp_path / "install" / ".env"
    env_file.write_text("SMTP_USER=x\n")
    env_file.chmod(0o000)
    try:
        assert cli.main(["--config", str(cfg), "worker", "--once"]) == 2
        err = capsys.readouterr().err
        assert f"ERROR: cannot read {env_file}" in err and "Traceback" not in err
    finally:
        env_file.chmod(0o600)
