"""Ordered provider chain with per-provider retries."""
import time


class Skip(Exception):
    """Provider unavailable (e.g. no API key): move on without retrying."""


def chain(steps, *, attempts=3, sleep=time.sleep, log=print):
    for name, call in steps:
        for attempt in range(1, attempts + 1):
            try:
                return call(), name
            except Skip as e:
                log(f"{name}: skipped ({e})")
                break
            except Exception as e:  # provider failures of any kind fall through
                # type and HTTP status only: messages of provider/parse errors can carry content
                status = getattr(e, "status", None)
                log(f"{name}: attempt {attempt}/{attempts} failed: {type(e).__name__}"
                    + (f" (HTTP {status})" if isinstance(status, int) else ""))
                if attempt < attempts:
                    sleep(2 ** attempt)
    return None


def require_key(key_env, env):
    """None when the endpoint needs no key, the key when set; Skip when a named key is missing."""
    if key_env is None:
        return None
    value = (env.get(key_env) or "").strip()
    if not value:
        raise Skip(f"no {key_env}")
    return value
