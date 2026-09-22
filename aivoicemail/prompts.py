"""Prompt templates (prompts/<lang>.toml), the prompt list per line and the transparency check."""
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .config import PROMPT_KEYS

MENU_INTRO_BREAK_MS = 350
MENU_OPTION_BREAK_MS = 450
MENU_LAST_BREAK_MS = 200


class TemplateError(Exception):
    pass


@dataclass(frozen=True)
class Template:
    lang: str
    transparency: tuple[str, ...]
    text: dict[str, str]


@dataclass(frozen=True)
class Segment:
    lang: str
    text: str
    break_ms: int | None = None


@dataclass(frozen=True)
class Prompt:
    name: str
    line: str
    kind: str
    segments: tuple[Segment, ...]
    sentence_ms: int | None = None


def fill(text, **values) -> str:
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def load_templates(prompts_dir, overrides=None) -> dict[str, Template]:
    out = {}
    for path in sorted(Path(prompts_dir).glob("*.toml")):
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            raise TemplateError(f"{path.name}: {e}") from None
        text = dict(raw.get("text", {}))
        text.update((overrides or {}).get(path.stem, {}))
        missing = [k for k in PROMPT_KEYS if not isinstance(text.get(k), str) or not text[k].strip()]
        if missing:
            raise TemplateError(f"{path.name}: missing text keys {', '.join(missing)}")
        transparency = raw.get("transparency")
        if not isinstance(transparency, list) or not transparency or not all(isinstance(x, str) for x in transparency):
            raise TemplateError(f"{path.name}: transparency must be a non-empty list of phrases")
        out[path.stem] = Template(path.stem, tuple(transparency), text)
    return out


def _names(line) -> list[str]:
    if not line.has_menu:
        lang = line.menu[0]
        return [f"{line.id}-notice-{lang}", f"{line.id}-thanks-{lang}"]
    auto = line.default_language == "auto"
    return ([f"{line.id}-menu"] + [f"{line.id}-notice-{l}" for l in line.menu]
            + ([f"{line.id}-notice-auto"] if auto else [])
            + [f"{line.id}-thanks-{l}" for l in line.menu]
            + ([f"{line.id}-thanks-auto"] if auto else []))


def prompt_names(cfg) -> list[str]:
    return [name for line in cfg.lines for name in _names(line)]


def build_prompts(cfg, templates) -> list[Prompt]:
    out = []
    for line in cfg.lines:
        values = {"company": cfg.company.name, "privacy_url": line.privacy_url}
        text = lambda lang, key: fill(templates[lang].text[key], **values)
        made = {}
        for lang in line.menu:
            made[f"{line.id}-notice-{lang}"] = Prompt(f"{line.id}-notice-{lang}", line.id, "notice",
                                                     (Segment(lang, text(lang, "notice")),), cfg.tts.sentence_ms)
            made[f"{line.id}-thanks-{lang}"] = Prompt(f"{line.id}-thanks-{lang}", line.id, "thanks",
                                                     (Segment(lang, text(lang, "thanks")),))
        if line.has_menu:
            segs = [Segment(line.menu[0], f"{cfg.company.name}.", MENU_INTRO_BREAK_MS)]
            for digit, lang in enumerate(line.menu, start=1):
                last = digit == len(line.menu)
                segs.append(Segment(lang, fill(text(lang, "menu_option"), digit=digit),
                                    MENU_LAST_BREAK_MS if last else MENU_OPTION_BREAK_MS))
            made[f"{line.id}-menu"] = Prompt(f"{line.id}-menu", line.id, "menu", tuple(segs))
            auto = [Segment(lang, text(lang, "notice_short")) for lang in line.menu]
            auto.append(Segment(line.menu[-1], text(line.menu[-1], "after_tone")))
            made[f"{line.id}-notice-auto"] = Prompt(f"{line.id}-notice-auto", line.id, "notice_auto", tuple(auto),
                                                   cfg.tts.sentence_ms)
            made[f"{line.id}-thanks-auto"] = Prompt(f"{line.id}-thanks-auto", line.id, "thanks_auto",
                                                   tuple(Segment(l, text(l, "thanks_short")) for l in line.menu))
        out += [made[name] for name in _names(line)]
    return out


def transparency_problems(cfg, templates) -> list[str]:
    problems = []
    for line in cfg.lines:
        values = {"company": cfg.company.name, "privacy_url": line.privacy_url}
        keys = ["notice"] + (["notice_short"] if line.has_menu and line.default_language == "auto" else [])
        for lang in line.menu:
            tpl = templates.get(lang)
            if tpl is None:
                problems.append(f"line {line.id}: no prompt template prompts/{lang}.toml")
                continue
            for key in keys:
                spoken = fill(tpl.text[key], **values).casefold()
                for phrase in tpl.transparency:
                    element = fill(phrase, **values)
                    if element.casefold() not in spoken:
                        problems.append(f"line {line.id}: {lang} {key} lacks transparency element {element!r}")
    return problems
