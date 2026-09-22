"""LLM chain: endpoints in config order, retries per endpoint, invalid output counts as failure."""
import time

from .. import retry
from . import anthropic as anthropic_adapter
from . import openai_compatible
from .schema import validate


def summarise(transcript, meta, *, email_language, cfg, env, sleep=time.sleep, log=print):
    def call(endpoint):
        key = retry.require_key(endpoint.key_env, env)
        adapter = anthropic_adapter if endpoint.kind == "anthropic" else openai_compatible
        return validate(adapter.summarise(endpoint, key, transcript, meta, email_language))

    steps = [(name, lambda e=cfg.endpoints[name]: call(e)) for name in cfg.chain]
    return retry.chain(steps, sleep=sleep, log=log)
