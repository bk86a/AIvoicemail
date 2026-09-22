"""STT chain: providers in config order, retries per provider, a missing key skips a provider."""
import time

from .. import retry
from ..config import LOCAL_STT
from . import openai_compatible
from .base import nonempty


def transcribe(wav, lang, candidates, *, cfg, env, whisper, sleep=time.sleep, log=print):
    def local():
        if whisper is None:
            raise retry.Skip("local model not loaded")
        return nonempty(whisper.transcribe(wav, lang, candidates))

    def remote(endpoint):
        key = retry.require_key(endpoint.key_env, env)
        return openai_compatible.transcribe(endpoint, key, wav, lang)

    steps = []
    for name in cfg.chain:
        if name == LOCAL_STT:
            steps.append((name, local))
        else:
            steps.append((name, lambda e=cfg.endpoints[name]: remote(e)))
    return retry.chain(steps, sleep=sleep, log=log)
