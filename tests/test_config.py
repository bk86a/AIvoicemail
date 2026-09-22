import os
from pathlib import Path

import pytest

from aivoicemail import config
from conftest import EXAMPLE, ROOT

TEXT = EXAMPLE.read_text(encoding="utf-8")


def load_text(tmp_path, text):
    p = tmp_path / "install" / "config" / "aivoicemail.toml"
    p.parent.mkdir(parents=True)
    p.write_text(text, encoding="utf-8")
    return config.load(p)


def problems_for(tmp_path, old, new, append=False):
    text = TEXT + new if append else TEXT.replace(old, new, 1)
    assert append or old in TEXT, old
    with pytest.raises(config.ConfigError) as e:
        load_text(tmp_path, text)
    return e.value.problems


def test_example_loads(example_cfg):
    c = example_cfg
    assert c.company.name == "ACME BV"
    assert c.trunk.signalling_ranges == ("46.19.208.0/21", "185.238.172.0/22")
    assert c.trunk.public_ip == "203.0.113.10" and c.trunk.local_net == "10.0.0.0/24"
    assert c.trunk.bind == "0.0.0.0:5060" and c.trunk.sip_port == 5060 and c.trunk.allow_local_test is True
    be, pl = c.lines
    assert (be.id, be.did, be.mailbox, be.email_language) == ("be", "3220000001", "info@acme.example", "en")
    assert be.menu == ("nl", "fr", "en") and be.has_menu and be.default_language == "auto"
    assert pl.menu == ("pl",) and not pl.has_menu and pl.default_language == "pl"
    assert be.privacy_url == "acme.example/privacy" and pl.privacy_url == "acme.example/privacy"
    assert c.recording == config.Recording(180, 5, 2.0)
    assert c.stt.chain == ("whisper_local", "remote") and c.stt.whisper_model == "large-v3"
    assert c.stt.endpoints["remote"].base_url == "https://api.mistral.ai/v1"
    assert c.stt.endpoints["remote"].key_env == "STT_API_KEY"
    assert c.llm.chain == ("primary",) and c.llm.endpoints["primary"].structured == "json_schema"
    assert c.mail.smtp_port == 587 and c.mail.smtp_security == "starttls"
    assert c.mail.from_address == "voicemail@acme.example" and c.mail.alert_to == "admin@acme.example"
    assert c.tts.engine == "piper" and c.tts.voices["nl"] == "nl_BE-nathalie-medium"
    assert c.tts.pronunciation["ACME"]["en"] == "ˈæk.mi"
    assert c.retention == config.Retention(90, False)
    assert c.worker == config.Worker(30, 60, 15, 5)
    assert c.spool.backend == "local" and c.spool.path == Path("/srv/aivoicemail/spool")
    assert c.firewall.allow_tcp == (22,)
    assert c.paths.root == ROOT and c.paths.prompts_dir == ROOT / "prompts"
    assert c.paths.data_dir == Path("/var/lib/aivoicemail") and c.paths.work_dir == Path("/run/aivoicemail")


def test_line_lookup(example_cfg):
    assert example_cfg.line("pl").did == "48320000001"
    assert example_cfg.line("zz") is None
    assert example_cfg.line_by_did("3220000001").id == "be"


@pytest.mark.parametrize("old,new,needle", [
    ('did = "3220000001"', 'did = "32200a0001"', "did"),
    ('did = "48320000001"', 'did = "3220000001"', "duplicate did"),
    ('id = "pl"', 'id = "be"', "duplicate id"),
    ('"46.19.208.0/21", "185.238.172.0/22"]\nmedia', '"46.19.208.1/21", "185.238.172.0/22"]\nmedia', "CIDR"),
    ('chain = ["primary"]', 'chain = ["primary", "tertiary"]', "tertiary"),
    ('default_language = "auto"', 'default_language = "de"', "default_language"),
    ('id = "be"', 'id = "BE"', ".id"),
    ('structured = "json_schema"', 'structured = "xml"', "structured"),
    ('key_env = "LLM_API_KEY"', 'key_env = "llm_key"', "key_env"),
    ('menu = ["nl", "fr", "en"]', 'menu = ["nl", "nl"]', "duplicate language"),
    ('public_ip = "203.0.113.10"', 'public_ip = "203.0.113"', "public_ip"),
    ('engine = "piper"', 'engine = "espeak"', "engine"),
])
def test_invalid_values(tmp_path, old, new, needle):
    assert any(needle in p for p in problems_for(tmp_path, old, new)), needle


def test_unknown_top_level_key(tmp_path):
    assert any("unknown key 'typo'" in p for p in problems_for(tmp_path, None, "\n[typo]\nx = 1\n", append=True))


def test_ssh_spool_requires_target(tmp_path):
    problems = problems_for(tmp_path, None, '\n[spool]\nbackend = "ssh"\n', append=True)
    assert any("ssh_target" in p for p in problems)


def test_all_problems_reported_together(tmp_path):
    text = TEXT.replace('did = "3220000001"', 'did = "x"').replace('structured = "json_schema"', 'structured = "x"')
    with pytest.raises(config.ConfigError) as e:
        load_text(tmp_path, text)
    assert len(e.value.problems) >= 2


def test_relative_paths_resolve_against_install_root(tmp_path):
    c = load_text(tmp_path, TEXT + '\n[paths]\ngenerated_dir = "gen"\ndata_dir = "/data"\n')
    assert c.paths.generated_dir == tmp_path / "install" / "gen"
    assert c.paths.data_dir == Path("/data")


def test_prompt_text_overrides(tmp_path):
    c = load_text(tmp_path, TEXT + '\n[prompt_text.en]\nthanks = "Bye."\n')
    assert c.prompt_text["en"]["thanks"] == "Bye."
    with pytest.raises(config.ConfigError):
        load_text(tmp_path / "b", TEXT + '\n[prompt_text.en]\nthank_you = "Bye."\n')


def test_load_env_parses_file_and_environment_wins(tmp_path):
    p = tmp_path / ".env"
    p.write_text('# comment\nA=1\nexport B="two words"\nC=\'x\'\n\nD=from-file\n', encoding="utf-8")
    env = config.load_env(p, {"D": "from-env"})
    assert env == {"A": "1", "B": "two words", "C": "x", "D": "from-env"}


def test_load_env_missing_file_uses_environment_only(tmp_path):
    assert config.load_env(tmp_path / "none", {"X": "1"}) == {"X": "1"}


def test_load_env_rejects_garbage(tmp_path):
    p = tmp_path / ".env"
    p.write_text("not a pair\n", encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.load_env(p, {})


def test_secret():
    assert config.secret({"K": " v "}, "K") == "v"
    assert config.secret({"K": ""}, "K") is None
    assert config.secret({}, "K") is None
    assert config.secret({"K": "v"}, None) is None
