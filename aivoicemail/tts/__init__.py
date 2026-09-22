"""TTS engines and render-prompts: every prompt of every line to <out>/sounds/vm/<name>.wav."""
import shutil
from pathlib import Path

from .. import prompts as prompts_mod
from ..config import secret
from .audio import check_format, tone, wav_bytes

BEEP = wav_bytes(tone(250, 1000))  # Record() plays "beep" (sounds/en/beep.wav); no Asterisk sound package ships


class RenderError(Exception):
    pass


def build_engine(cfg, env):
    if cfg.tts.engine == "placeholder":
        from .placeholder import PlaceholderEngine
        return PlaceholderEngine()
    if cfg.tts.engine == "azure":
        from .azure import AzureEngine
        key = secret(env, cfg.tts.azure_key_env)
        if not key:
            raise RenderError(f"{cfg.tts.azure_key_env} is not set")
        return AzureEngine(cfg.tts.azure_region, key, cfg.tts.voices, cfg.tts.pronunciation)
    from .piper import PiperEngine
    return PiperEngine(cfg.tts.voices, Path(cfg.paths.data_dir) / "voices", cfg.tts.pronunciation)


def render_all(cfg, env, out_dir, *, only=None, engine=None, log=print) -> list[Path]:
    templates = prompts_mod.load_templates(cfg.paths.prompts_dir, cfg.prompt_text)
    problems = prompts_mod.transparency_problems(cfg, templates)
    if problems:
        raise RenderError("; ".join(problems))
    sounds = Path(out_dir) / "sounds"
    (sounds / "vm").mkdir(parents=True, exist_ok=True)
    (sounds / "en").mkdir(parents=True, exist_ok=True)
    (sounds / "en" / "beep.wav").write_bytes(BEEP)
    engine = engine or build_engine(cfg, env)
    written = []
    for prompt in prompts_mod.build_prompts(cfg, templates):
        if only and prompt.name not in only:
            continue
        target = sounds / "vm" / f"{prompt.name}.wav"
        override = Path(cfg.paths.overrides_dir) / f"{prompt.name}.wav"
        if override.is_file():
            problem = check_format(override)
            if problem:
                raise RenderError(f"{override}: {problem}")
            shutil.copyfile(override, target)
            log(f"{prompt.name}: override copied")
        else:
            target.write_bytes(engine.render(prompt))
            problem = check_format(target)
            if problem:
                raise RenderError(f"{prompt.name}: engine output {problem}")
            log(f"{prompt.name}: rendered")
        written.append(target)
    return written
