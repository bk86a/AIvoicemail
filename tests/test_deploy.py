import re

from aivoicemail import generate
from conftest import ROOT

DEPLOY = ROOT / "deploy"


def text(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_nftables_example_is_the_generated_ruleset(example_cfg):
    assert text("deploy/nftables/aivoicemail.nft") == generate.files(example_cfg)["nftables/aivoicemail.nft"]


def test_nftables_policy_drop_and_trunk_only():
    nft = text("deploy/nftables/aivoicemail.nft")
    assert "policy drop;" in nft and "table inet aivoicemail {" in nft
    assert "ip saddr @trunk_sig_v4 udp dport 5060 accept" in nft
    assert not re.search(r"^\s*udp dport 5060 accept", nft, re.M)  # never SIP from anywhere


def test_telephony_service_hardening():
    c = text("deploy/compose.telephony.yaml")
    for line in ("read_only: true", "cap_drop: [ALL]", 'security_opt: ["no-new-privileges:true"]',
                 'user: "5060:5060"', "network_mode: host", 'max-size: "10m"'):
        assert line in c, line
    assert "privileged" not in c and "cap_add" not in c


def test_worker_services_share_the_hardening_anchor():
    c = text("deploy/compose.worker.yaml")
    anchor = c.split("services:")[0]
    for line in ("read_only: true", "cap_drop: [ALL]", 'security_opt: ["no-new-privileges:true"]',
                 'user: "10001:5060"', 'max-size: "10m"'):
        assert line in anchor, line
    assert c.count("<<: *hardening") == 2
    assert "privileged" not in c and "cap_add" not in c
    assert "/run/aivoicemail:uid=10001,gid=5060,mode=0700" in c


def test_single_host_includes_both():
    assert "- compose.telephony.yaml" in text("deploy/compose.yaml") and "- compose.worker.yaml" in text("deploy/compose.yaml")
    assert "- deploy/compose.yaml" in text("compose.yaml")


def test_worker_dockerfile():
    d = text("Dockerfile")
    assert re.search(r"^FROM python:3\.12-slim-bookworm@sha256:[0-9a-f]{64}$", d, re.M)
    assert "--require-hashes -r requirements.lock" in d and "USER 10001:5060" in d
    assert 'ENTRYPOINT ["aivoicemail"]' in d


def test_dockerignore_keeps_secrets_out_of_the_image():
    ignored = text(".dockerignore").split()
    for entry in (".env", "config/aivoicemail.toml", "secrets", "generated", "data", ".git"):
        assert entry in ignored, entry


def test_cdr_logrotate_matches_default_retention():
    lr = "\n".join(l for l in text("deploy/logrotate/aivoicemail-cdr").splitlines() if not l.startswith("#"))
    assert "rotate 90" in lr and "maxage 90" in lr and "nocreate" in lr and "copytruncate" not in lr
