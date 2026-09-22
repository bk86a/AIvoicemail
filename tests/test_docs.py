from conftest import ROOT

DOCS = ["README.md", "SECURITY.md", "CHANGELOG.md", "docs/install.md", "docs/configuration.md",
        "docs/carriers.md", "docs/gdpr-ai-act.md"]
CONFIG_KEYS = {
    "company": ["name", "privacy_url_default"],
    "trunk": ["provider", "signalling_ranges", "media_ranges", "public_ip", "local_net", "bind", "media_address",
              "allow_local_test"],
    "line": ["id", "did", "mailbox", "email_language", "menu", "default_language", "privacy_url"],
    "recording": ["max_seconds", "silence_seconds", "min_speech_seconds"],
    "stt": ["chain", "whisper_model", "whisper_threads"],
    "endpoint": ["kind", "base_url", "model", "key_env", "structured", "auth"],
    "mail": ["smtp_host", "smtp_port", "smtp_security", "smtp_user_env", "smtp_password_env", "from", "from_name",
             "alert_to"],
    "tts": ["engine", "voices", "pronunciation", "sentence_ms", "azure"],
    "retention": ["call_log_days", "cdr"],
    "worker": ["poll_seconds", "stale_minutes", "unreachable_minutes", "max_failures"],
    "spool": ["backend", "path", "ssh_target", "ssh_key", "known_hosts"],
    "firewall": ["allow_tcp", "allow_udp"],
    "paths": ["data_dir", "work_dir", "prompts_dir", "overrides_dir", "generated_dir"],
    "prompt_text": ["menu_option", "notice", "notice_short", "after_tone", "thanks", "thanks_short"],
}


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_docs_exist_without_em_dashes():
    for path in DOCS:
        assert "—" not in read(path), path


def test_readme_quickstart_follows_the_spec_sequence():
    readme = read("README.md")
    steps = ["cp config/aivoicemail.example.toml", "cp .env.example .env", "./aivm check", "./aivm render-prompts",
             "docker compose up -d", "./aivm test-call"]
    positions = [readme.index(s) for s in steps]
    assert positions == sorted(positions)


def test_configuration_reference_covers_every_key():
    doc = read("docs/configuration.md")
    for section, keys in CONFIG_KEYS.items():
        for key in keys:
            assert f"`{key}`" in doc, f"{section}.{key}"


def test_carriers_documents_telephone_event_and_ranges():
    doc = read("docs/carriers.md")
    assert "telephone-event" in doc and "46.19.208.0/21" in doc and "185.238.172.0/22" in doc


def test_gdpr_notes_are_a_checklist_not_legal_advice():
    doc = read("docs/gdpr-ai-act.md")
    assert "not legal advice" in doc and doc.count("- [ ]") >= 8


def test_install_documents_firewall_rollback_and_split_mode():
    doc = read("docs/install.md")
    for part in ("systemd-run --unit=aivm-nft-rollback", 'restrict,from="', 'command="/usr/local/bin/vm-spool"',
                 "ssh-keyscan", "git pull && docker compose up -d --build"):
        assert part in doc, part


def test_security_policy_has_private_reporting():
    assert "private vulnerability reporting" in " ".join(read("SECURITY.md").split())


def test_docs_caveat_pjsip_logs_from_uri_of_unidentified_sources():
    for path in ("README.md", "docs/gdpr-ai-act.md", "SECURITY.md"):
        assert "From URI" in read(path), path


def test_install_updates_mention_possible_duplicate_email():
    updates = read("docs/install.md").split("## Updates")[1].split("## ")[0]
    assert "restart" in updates and "one email" in updates


def test_piper_voice_licences_documented():
    for path in ("docs/configuration.md", "README.md"):
        assert "licence" in read(path) and "Piper" in read(path), path
    assert "voice" in read("docs/configuration.md").split("licence")[0][-200:]


def test_security_mentions_spoofed_invites():
    doc = read("SECURITY.md")
    assert "spoofed" in doc and "missed-call" in doc


def test_local_llm_via_host_docker_internal_documented():
    doc = read("docs/install.md")
    assert "http://host.docker.internal:11434/v1" in doc and "OLLAMA_HOST" in doc
    assert "`[firewall] allow_tcp`" in doc and "docker0" in doc
    assert "http://host.docker.internal:11434/v1" in read("docs/configuration.md")
    assert "127.0.0.1:11434" not in read("docs/configuration.md") + read("config/aivoicemail.example.toml")


def test_gdpr_lists_what_is_sent_to_processors():
    doc = read("docs/gdpr-ai-act.md")
    assert "transcript only" in doc and "caller number" in doc.split("## Processors")[1]


def test_orphan_alert_documented():
    assert "orphan" in read("docs/install.md") and "orphan" in read("docs/configuration.md")
