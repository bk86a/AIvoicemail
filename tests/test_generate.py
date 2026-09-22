import dataclasses
import os
import re

import pytest

from aivoicemail import cli, config, generate
from conftest import EXAMPLE, ROOT

GOLDEN = ROOT / "tests" / "golden"
CASES = {"example": EXAMPLE, "single": GOLDEN / "single" / "config" / "aivoicemail.toml"}


@pytest.mark.parametrize("case", sorted(CASES))
def test_golden_files(case):
    got = generate.files(config.load(CASES[case]))
    assert sorted(got) == ["asterisk/cdr.conf", "asterisk/extensions-lines.conf", "asterisk/modules-cdr.conf",
                           "asterisk/pjsip-trunk.conf", "nftables/aivoicemail.nft", "prompts.txt"]
    if os.environ.get("UPDATE_GOLDEN") == "1":
        for rel, content in got.items():
            (GOLDEN / case / rel).parent.mkdir(parents=True, exist_ok=True)
            (GOLDEN / case / rel).write_text(content, encoding="utf-8")
    for rel, content in got.items():
        assert (GOLDEN / case / rel).read_text(encoding="utf-8") == content, f"{case}/{rel}"


def test_security_properties(example_cfg):
    out = generate.files(example_cfg)
    pjsip, ext = out["asterisk/pjsip-trunk.conf"], out["asterisk/extensions-lines.conf"]
    for bad in ("type = auth", "type = registration", "type = aor", "anonymous", "tcp", "tls"):
        assert bad not in pjsip, bad
    assert "dtmf_mode = auto" in pjsip and "protocol = udp" in pjsip
    for bad in ("Dial(", "Originate", "Queue(", "Page(", "FollowMe", "System(", "${EXTEN}", "CALLERID"):
        assert bad not in ext, bad
    assert ext.count("Hangup(1)") == 1


def test_dids_in_dialplan_are_digits(example_cfg):
    ext = generate.extensions_lines(example_cfg)
    dids = re.findall(r'"\$\{D\}" = "([^"]*)"', ext)
    assert dids == ["3220000001", "48320000001"]


def test_media_address_option(example_cfg):
    cfg = dataclasses.replace(example_cfg, trunk=dataclasses.replace(example_cfg.trunk, media_address="10.0.0.5"))
    assert "media_address = 10.0.0.5\nbind_rtp_to_media_address = yes\n" in generate.pjsip_trunk(cfg)


def test_bind_ipv6_bracketed_round_trips(example_cfg):
    cfg = dataclasses.replace(example_cfg, trunk=dataclasses.replace(example_cfg.trunk, bind="[2001:db8::1]:5070"))
    assert "bind = [2001:db8::1]:5070\n" in generate.pjsip_trunk(cfg)


@pytest.mark.parametrize("bad_trunk", [
    dict(public_ip="203.0.113.10%evil"),
    dict(media_address="10.0.0.5;evil"),
    dict(local_net="10.0.0.0/24 evil"),
    dict(signalling_ranges=("46.19.208.0%evil/21",)),
    dict(bind="0.0.0.0}evil:5060"),
])
def test_pjsip_trunk_rejects_invalid_characters(example_cfg, bad_trunk):
    cfg = dataclasses.replace(example_cfg, trunk=dataclasses.replace(example_cfg.trunk, **bad_trunk))
    with pytest.raises(ValueError):
        generate.pjsip_trunk(cfg)


def test_extensions_lines_rejects_invalid_line_id(example_cfg):
    be, pl = example_cfg.lines
    bad = dataclasses.replace(be, id="be;evil")
    cfg = dataclasses.replace(example_cfg, lines=(bad, pl))
    with pytest.raises(ValueError):
        generate.extensions_lines(cfg)


def test_extensions_lines_rejects_invalid_did(example_cfg):
    be, pl = example_cfg.lines
    bad = dataclasses.replace(be, did="322000}evil")
    cfg = dataclasses.replace(example_cfg, lines=(bad, pl))
    with pytest.raises(ValueError):
        generate.extensions_lines(cfg)


def test_nftables_rejects_invalid_cidr(example_cfg):
    cfg = dataclasses.replace(example_cfg, trunk=dataclasses.replace(
        example_cfg.trunk, signalling_ranges=("46.19.208.0%evil/21",)))
    with pytest.raises(ValueError):
        generate.nftables(cfg)


@pytest.mark.parametrize("field", ["signalling_ranges", "media_ranges", "local_net"])
def test_bare_ip_range_is_not_rejected(example_cfg, field):
    """A bare IP (no /prefix), e.g. a single SBC address, is config-valid (ipaddress.ip_network
    treats it as an implicit host route) so generate's re-check must not reject it either."""
    kwargs = {field: "46.19.208.1"} if field == "local_net" else {field: ("46.19.208.1",)}
    cfg = dataclasses.replace(example_cfg, trunk=dataclasses.replace(example_cfg.trunk, **kwargs))
    generate.pjsip_trunk(cfg)  # must not raise
    if field != "local_net":
        generate.nftables(cfg)  # must not raise


def test_pjsip_trunk_bare_ip_signalling_range_keeps_bare_match(example_cfg):
    cfg = dataclasses.replace(example_cfg, trunk=dataclasses.replace(
        example_cfg.trunk, signalling_ranges=("46.19.208.1",)))
    assert "match = 46.19.208.1\n" in generate.pjsip_trunk(cfg)


def test_nftables_bare_ip_signalling_range_becomes_slash_32(example_cfg):
    cfg = dataclasses.replace(example_cfg, trunk=dataclasses.replace(
        example_cfg.trunk, signalling_ranges=("46.19.208.1",), media_ranges=("46.19.208.1",)))
    assert "46.19.208.1/32" in generate.nftables(cfg)


def test_bare_ipv6_range_normalises_to_slash_128(example_cfg):
    cfg = dataclasses.replace(example_cfg, trunk=dataclasses.replace(
        example_cfg.trunk, signalling_ranges=("2001:db8::1",), media_ranges=("2001:db8::1",)))
    generate.pjsip_trunk(cfg)  # must not raise
    assert "2001:db8::1/128" in generate.nftables(cfg)


def test_nftables_signalling_range_error_names_field(example_cfg):
    cfg = dataclasses.replace(example_cfg, trunk=dataclasses.replace(
        example_cfg.trunk, signalling_ranges=("46.19.208.0%evil/21",)))
    with pytest.raises(ValueError, match="trunk.signalling_ranges"):
        generate.nftables(cfg)


def test_nftables_media_range_error_names_field(example_cfg):
    cfg = dataclasses.replace(example_cfg, trunk=dataclasses.replace(
        example_cfg.trunk, media_ranges=("46.19.208.0%evil/21",)))
    with pytest.raises(ValueError, match="trunk.media_ranges"):
        generate.nftables(cfg)


def test_write_all(example_cfg, tmp_path):
    paths = generate.write_all(example_cfg, tmp_path)
    assert (tmp_path / "asterisk" / "pjsip-trunk.conf") in paths
    assert (tmp_path / "prompts.txt").read_text().splitlines()[0] == "be-menu"


def install(tmp_path):
    conf = tmp_path / "install" / "config" / "aivoicemail.toml"
    conf.parent.mkdir(parents=True)
    (tmp_path / "install" / "prompts").symlink_to(ROOT / "prompts")
    conf.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    return conf


def test_cli_generate(tmp_path):
    assert cli.main(["--config", str(install(tmp_path)), "generate", "--out", str(tmp_path / "gen")]) == 0
    got = (tmp_path / "gen" / "asterisk" / "extensions-lines.conf").read_text()
    assert got == (GOLDEN / "example" / "asterisk" / "extensions-lines.conf").read_text()


def test_render_prompts_also_generates(tmp_path):
    out = tmp_path / "gen"
    assert cli.main(["--config", str(install(tmp_path)), "render-prompts", "--engine", "placeholder", "--out", str(out)]) == 0
    assert (out / "asterisk" / "pjsip-trunk.conf").is_file() and (out / "sounds" / "vm" / "be-menu.wav").is_file()


def _boom(cfg, out):
    raise ValueError("generate: refusing to interpolate invalid trunk.bind: 'x'")


def test_cli_generate_reports_generate_value_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(generate, "write_all", _boom)
    rc = cli.main(["--config", str(install(tmp_path)), "generate", "--out", str(tmp_path / "gen")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "ERROR: generate: refusing to interpolate invalid trunk.bind" in err
    assert "Traceback" not in err


def test_cli_render_prompts_reports_generate_value_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(generate, "write_all", _boom)
    rc = cli.main(["--config", str(install(tmp_path)), "render-prompts", "--engine", "placeholder",
                   "--out", str(tmp_path / "gen")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "ERROR: generate: refusing to interpolate invalid trunk.bind" in err
    assert "Traceback" not in err


def _with_cdr(cfg, on):
    return dataclasses.replace(cfg, retention=dataclasses.replace(cfg.retention, cdr=on))


def test_modules_cdr_include_noloads_cdr_csv_when_cdr_off(example_cfg):
    off = generate.files(_with_cdr(example_cfg, False))["asterisk/modules-cdr.conf"]
    on = generate.files(_with_cdr(example_cfg, True))["asterisk/modules-cdr.conf"]
    assert "noload = cdr_csv.so" in off.splitlines()
    assert not [l for l in on.splitlines() if l.strip() and not l.lstrip().startswith(";")]


def test_local_test_adds_loopback_local_net_when_public_ip_set(example_cfg):
    pjsip = generate.pjsip_trunk(example_cfg)  # public_ip set, allow_local_test defaults to true
    assert "local_net = 10.0.0.0/24\n" in pjsip and "local_net = 127.0.0.0/8\n" in pjsip
    no_test = dataclasses.replace(example_cfg, trunk=dataclasses.replace(example_cfg.trunk, allow_local_test=False))
    assert "127.0.0.0/8" not in generate.pjsip_trunk(no_test)
    no_nat = dataclasses.replace(example_cfg, trunk=dataclasses.replace(example_cfg.trunk, public_ip=None))
    assert "127.0.0.0/8" not in generate.pjsip_trunk(no_nat)


def _with_llm_url(cfg, url):
    ep = config.Endpoint(name="local", model="m", base_url=url, structured="json_object")
    return dataclasses.replace(cfg, llm=config.Llm(chain=("local",), endpoints={"local": ep}))


def test_nftables_allows_docker_bridges_to_host_docker_internal_endpoint(example_cfg):
    nft = generate.nftables(_with_llm_url(example_cfg, "http://host.docker.internal:11434/v1"))
    assert '    iifname "docker0" tcp dport 11434 accept\n' in nft
    assert '    iifname "br-*" tcp dport 11434 accept\n' in nft


def test_nftables_host_docker_internal_default_port_and_stt(example_cfg):
    ep = config.Endpoint(name="local", model="m", base_url="http://host.docker.internal/v1")
    cfg = dataclasses.replace(example_cfg, stt=dataclasses.replace(example_cfg.stt, endpoints={"local": ep}))
    assert 'iifname "docker0" tcp dport 80 accept' in generate.nftables(cfg)


def test_nftables_no_bridge_rule_for_remote_endpoints(example_cfg):
    assert "iifname" not in generate.nftables(example_cfg)
    assert "iifname" not in generate.nftables(_with_llm_url(example_cfg, "http://127.0.0.1:11434/v1"))


def test_nftables_allows_dhcpv6_client(example_cfg):
    nft = generate.nftables(example_cfg)
    assert "    udp sport 67 udp dport 68 accept\n    udp sport 547 udp dport 546 accept\n" in nft
