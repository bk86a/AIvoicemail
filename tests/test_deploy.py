import os
import re
import subprocess

from aivoicemail import generate
from conftest import ROOT

DEPLOY = ROOT / "deploy"


def text(path):
    return (ROOT / path).read_text(encoding="utf-8")


def aivm(tmp_path, *args):
    """Run ./aivm with a fake `docker` on PATH that just records its argv, to check routing."""
    calls = tmp_path / "docker_calls"
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\n', encoding="utf-8")
    fake_docker.chmod(0o755)
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}")
    subprocess.run([str(ROOT / "aivm"), *args], cwd=ROOT, env=env, check=True, capture_output=True)
    return calls.read_text(encoding="utf-8") if calls.exists() else ""


def test_aivm_routes_test_call_to_the_testcall_service(tmp_path):
    assert "run --rm testcall --line pl" in aivm(tmp_path, "test-call", "--line", "pl")


def test_aivm_routes_other_commands_to_tools(tmp_path):
    assert "run --rm tools check" in aivm(tmp_path, "check")
    assert "testcall" not in aivm(tmp_path, "render-prompts")


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
    assert c.count("<<: *hardening") == 3
    assert "privileged" not in c
    assert "pid: host" not in c
    assert "/run/aivoicemail:uid=10001,gid=5060,mode=0700" in c


def test_only_testcall_gets_net_raw_for_sipp():
    c = text("deploy/compose.worker.yaml")
    worker_block, rest = c.split("  worker:")[1].split("  tools:")
    tools_block, testcall_block = rest.split("  testcall:")
    assert "cap_add" not in worker_block and "security_opt" not in worker_block
    assert "cap_add" not in tools_block and "security_opt" not in tools_block
    assert "cap_add: [NET_RAW]" in testcall_block
    # no-new-privileges (from the shared anchor) is explicitly cleared for testcall only: it would
    # block the CAP_NET_RAW file capability on /usr/bin/sipp from taking effect even with cap_add above
    assert "security_opt: []" in testcall_block
    assert 'entrypoint: ["aivoicemail", "test-call"]' in testcall_block
    # worker and tools still get no-new-privileges via the anchor (neither block overrides it)
    assert '"no-new-privileges:true"' in c.split("services:")[0]


def test_worker_reaches_host_services_via_host_docker_internal():
    c = text("deploy/compose.worker.yaml")
    worker_block = c.split("  worker:")[1].split("  tools:")[0]
    assert 'extra_hosts: ["host.docker.internal:host-gateway"]' in worker_block


def test_dockerfile_strips_setuid_bits():
    d = text("Dockerfile")
    assert "find / -xdev -perm /6000 -type f -exec chmod a-s {} +" in d
    assert d.index("chmod a-s") > d.index("apt-get install")


def test_dockerfile_grants_sipp_raw_socket_capability():
    d = text("Dockerfile")
    assert "setcap cap_net_raw+ep /usr/bin/sipp" in d


def test_single_host_includes_both():
    assert "- compose.telephony.yaml" in text("deploy/compose.yaml") and "- compose.worker.yaml" in text("deploy/compose.yaml")
    assert "- deploy/compose.yaml" in text("compose.yaml")


def test_worker_dockerfile():
    d = text("Dockerfile")
    assert re.search(r"^FROM python:3\.12-slim-trixie@sha256:[0-9a-f]{64}$", d, re.M)
    assert "--require-hashes -r requirements.lock" in d and "USER 10001:5060" in d
    assert 'ENTRYPOINT ["aivoicemail"]' in d


def test_dockerignore_keeps_secrets_out_of_the_image():
    ignored = text(".dockerignore").split()
    for entry in (".env", "config/aivoicemail.toml", "secrets", "generated", "data", ".git"):
        assert entry in ignored, entry


def test_ci_sipp_job_has_a_timeout():
    ci = text(".github/workflows/ci.yml")
    assert "timeout-minutes: 40" in ci.split("  sipp:")[1]


def test_cdr_logrotate_matches_default_retention():
    lr = "\n".join(l for l in text("deploy/logrotate/aivoicemail-cdr").splitlines() if not l.startswith("#"))
    assert "rotate 90" in lr and "maxage 90" in lr and "nocreate" in lr and "copytruncate" not in lr
