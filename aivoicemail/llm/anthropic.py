"""Optional Anthropic adapter (install with the `anthropic` extra)."""
from ..retry import Skip
from .schema import SCHEMA, build_prompt, parse_summary


def summarise(endpoint, key, transcript, meta, email_language):
    try:
        import anthropic
    except ImportError:
        raise Skip("anthropic extra not installed") from None
    if not key:
        raise Skip("anthropic endpoint needs key_env")
    system, user = build_prompt(transcript, meta, email_language)
    client = anthropic.Anthropic(api_key=key, max_retries=0, timeout=120)
    response = client.messages.create(
        model=endpoint.model,
        max_tokens=2048,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("model refused")
    text = next(block.text for block in response.content if block.type == "text")
    return parse_summary(text, meta)
