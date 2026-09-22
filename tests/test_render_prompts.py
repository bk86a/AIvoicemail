import dataclasses

import pytest

from aivoicemail import cli, prompts
from aivoicemail.tts import RenderError, audio, render_all
from aivoicemail.tts.placeholder import PlaceholderEngine
from conftest import EXAMPLE, write_wav


def test_render_all_with_placeholder(cfg, tmp_path):
    out = tmp_path / "gen"
    written = render_all(cfg, {}, out, engine=PlaceholderEngine(), log=lambda *a: None)
    assert [p.stem for p in written] == prompts.prompt_names(cfg)
    for p in written:
        assert audio.check_format(p) is None
    assert audio.check_format(out / "sounds" / "en" / "beep.wav") is None


def test_override_is_copied_and_validated(cfg, tmp_path):
    write_wav(cfg.paths.overrides_dir / "be-menu.wav", 3)
    out = tmp_path / "gen"
    render_all(cfg, {}, out, engine=PlaceholderEngine(), only=["be-menu"], log=lambda *a: None)
    assert audio.duration(out / "sounds" / "vm" / "be-menu.wav") == 3.0
    write_wav(cfg.paths.overrides_dir / "be-menu.wav", 1, rate=16000)
    with pytest.raises(RenderError):
        render_all(cfg, {}, out, engine=PlaceholderEngine(), only=["be-menu"], log=lambda *a: None)


def test_refuses_when_transparency_is_broken(cfg, tmp_path):
    broken = dataclasses.replace(cfg, prompt_text={"en": {"notice": "Hi from {company}."}})
    with pytest.raises(RenderError):
        render_all(broken, {}, tmp_path / "gen", engine=PlaceholderEngine(), log=lambda *a: None)


def test_azure_engine_needs_key(cfg, tmp_path):
    from aivoicemail.tts import build_engine
    az = dataclasses.replace(cfg, tts=dataclasses.replace(cfg.tts, engine="azure", azure_region="r", azure_key_env="AZ_KEY"))
    with pytest.raises(RenderError):
        build_engine(az, {})


def test_cli_render_prompts_placeholder(tmp_path):
    conf = tmp_path / "install" / "config" / "aivoicemail.toml"
    conf.parent.mkdir(parents=True)
    (tmp_path / "install" / "prompts").symlink_to(EXAMPLE.parents[1] / "prompts")  # prompts_dir = <install>/prompts
    conf.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    out = tmp_path / "gen"
    assert cli.main(["--config", str(conf), "render-prompts", "--engine", "placeholder", "--out", str(out)]) == 0
    assert (out / "sounds" / "vm" / "pl-thanks-pl.wav").is_file()
