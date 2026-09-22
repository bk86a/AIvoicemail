import dataclasses

import pytest

from aivoicemail import prompts
from aivoicemail.config import PROMPT_KEYS
from conftest import ROOT

PROMPTS_DIR = ROOT / "prompts"


@pytest.fixture
def templates(example_cfg):
    return prompts.load_templates(PROMPTS_DIR, example_cfg.prompt_text)


def test_five_templates_complete(templates):
    assert sorted(templates) == ["de", "en", "fr", "nl", "pl"]
    for t in templates.values():
        assert set(PROMPT_KEYS) <= set(t.text)
        assert "{privacy_url}" in t.transparency and len(t.transparency) == 4
        assert "{digit}" in t.text["menu_option"]


def test_no_em_dashes_in_templates():
    for path in PROMPTS_DIR.glob("*.toml"):
        assert "—" not in path.read_text(encoding="utf-8"), path.name


@pytest.mark.parametrize("lang", ["de", "en", "fr", "nl", "pl"])
def test_each_template_satisfies_its_own_transparency_list(templates, lang):
    t = templates[lang]
    for key in ("notice", "notice_short"):
        text = prompts.fill(t.text[key], company="ACME BV", privacy_url="acme.example/privacy").casefold()
        for phrase in t.transparency:
            assert prompts.fill(phrase, privacy_url="acme.example/privacy").casefold() in text, (lang, key, phrase)
        assert "{company}" in t.text["notice"]


def test_fill_leaves_other_braces():
    assert prompts.fill("{company} {x}", company="A") == "A {x}"


def test_prompt_names(example_cfg):
    assert prompts.prompt_names(example_cfg) == [
        "be-menu", "be-notice-nl", "be-notice-fr", "be-notice-en", "be-notice-auto",
        "be-thanks-nl", "be-thanks-fr", "be-thanks-en", "be-thanks-auto", "pl-notice-pl", "pl-thanks-pl"]


def test_build_prompts_order_and_menu(example_cfg, templates):
    built = prompts.build_prompts(example_cfg, templates)
    assert [p.name for p in built] == prompts.prompt_names(example_cfg)
    menu = built[0]
    assert menu.kind == "menu" and menu.sentence_ms is None
    assert [(s.lang, s.text, s.break_ms) for s in menu.segments] == [
        ("nl", "ACME BV.", 350),
        ("nl", "Voor Nederlands, druk 1.", 450),
        ("fr", "Pour le français, appuyez sur 2.", 450),
        ("en", "For English, press 3.", 200),
    ]


def test_auto_notice_and_thanks(example_cfg, templates):
    by = {p.name: p for p in prompts.build_prompts(example_cfg, templates)}
    auto = by["be-notice-auto"]
    assert [s.lang for s in auto.segments] == ["nl", "fr", "en", "en"]
    assert auto.segments[-1].text == "Please speak after the tone." and auto.sentence_ms == 300
    assert [s.lang for s in by["be-thanks-auto"].segments] == ["nl", "fr", "en"]
    pl = by["pl-notice-pl"]
    assert pl.kind == "notice" and "ACME BV" in pl.segments[0].text and "acme.example/privacy" in pl.segments[0].text


def test_default_language_line_has_no_auto_prompts(example_cfg):
    be = dataclasses.replace(example_cfg.lines[0], default_language="nl")
    cfg = dataclasses.replace(example_cfg, lines=(be,))
    assert "be-notice-auto" not in prompts.prompt_names(cfg) and "be-thanks-auto" not in prompts.prompt_names(cfg)


def test_example_config_passes_transparency(example_cfg, templates):
    assert prompts.transparency_problems(example_cfg, templates) == []


def test_override_that_drops_an_element_is_reported(example_cfg):
    cfg = dataclasses.replace(example_cfg, prompt_text={"en": {"notice": "Hello from {company}, visit {privacy_url}."}})
    problems = prompts.transparency_problems(cfg, prompts.load_templates(PROMPTS_DIR, cfg.prompt_text))
    assert any("line be: en notice lacks transparency element 'recorded'" == p for p in problems)


def test_missing_template_is_reported(example_cfg, templates):
    it = dataclasses.replace(example_cfg.lines[1], menu=("it",), default_language="it")
    cfg = dataclasses.replace(example_cfg, lines=(it,))
    assert prompts.transparency_problems(cfg, templates) == ["line pl: no prompt template prompts/it.toml"]


def test_broken_template_raises(tmp_path):
    (tmp_path / "xx.toml").write_text('transparency = ["a"]\n[text]\nnotice = "a"\n', encoding="utf-8")
    with pytest.raises(prompts.TemplateError):
        prompts.load_templates(tmp_path)
