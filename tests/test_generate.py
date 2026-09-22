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
    assert sorted(got) == ["asterisk/cdr.conf", "asterisk/extensions-lines.conf", "asterisk/pjsip-trunk.conf",
                           "nftables/aivoicemail.nft", "prompts.txt"]
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
