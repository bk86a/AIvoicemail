"""aivoicemail command line: check, generate, render-prompts, test-call, worker."""
import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError, load, load_env


def _truthy(value) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def load_all(args):
    cfg = load(args.config)
    env_file = Path(args.env_file) if args.env_file else cfg.paths.root / ".env"
    return cfg, load_env(env_file), env_file


def cmd_worker(args) -> int:
    from .processor import Processor
    from .worker import build, run
    cfg, env, _ = load_all(args)
    log = lambda msg: print(msg, flush=True)
    fake = args.fake_providers or _truthy(os.environ.get("AIVOICEMAIL_FAKE_PROVIDERS"))
    deps, closers = build(cfg, env, fake=fake, log=log)
    try:
        run(deps, Processor(deps), once=args.once)
    finally:
        for close in closers:
            close()
    return 0


def _add_worker(sub) -> None:
    p = sub.add_parser("worker", help="run the worker service (the container entry point)")
    p.add_argument("--once", action="store_true", help="process one cycle and exit")
    p.add_argument("--fake-providers", action="store_true", help="fixed transcript/summary, emails to the local outbox")
    p.set_defaults(func=cmd_worker)


def cmd_generate(args) -> int:
    from .generate import write_all
    cfg, _, _ = load_all(args)
    out = Path(args.out) if args.out else cfg.paths.generated_dir
    try:
        written = write_all(cfg, out)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    for path in written:
        print(f"wrote {path}")
    return 0


def cmd_render_prompts(args) -> int:
    import dataclasses
    from .generate import write_all
    from .tts import RenderError, render_all
    cfg, env, _ = load_all(args)
    if args.engine:
        cfg = dataclasses.replace(cfg, tts=dataclasses.replace(cfg.tts, engine=args.engine))
    out = Path(args.out) if args.out else cfg.paths.generated_dir
    try:
        write_all(cfg, out)  # keep the Asterisk files in step with the prompts they reference
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    try:
        written = render_all(cfg, env, out, only=args.only, log=print)
    except RenderError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"render-prompts: {len(written)} prompt(s) in {out / 'sounds' / 'vm'}; Asterisk files in {out / 'asterisk'}")
    return 0


def _add_render_prompts(sub) -> None:
    p = sub.add_parser("render-prompts", help="render every prompt to generated/sounds (TTS or overrides/)")
    p.add_argument("--out", help="output directory (default: [paths].generated_dir)")
    p.add_argument("--only", nargs="*", help="prompt names to render")
    p.add_argument("--engine", choices=["piper", "azure", "placeholder"], help="override [tts].engine")
    p.set_defaults(func=cmd_render_prompts)


def cmd_check(args) -> int:
    from .check import run_checks
    cfg, env, env_file = load_all(args)
    found = run_checks(cfg, env, env_file=env_file, online=args.online)
    for f in found:
        print(f"{f.level.upper()}: {f.message}")
    n_err = sum(f.level == "error" for f in found)
    print(f"check: {n_err} error(s), {len(found) - n_err} warning(s)")
    if not n_err:
        print("lines: " + ", ".join(f"{l.id} ({l.did})" for l in cfg.lines))
    return 1 if n_err else 0


def _add_check(sub) -> None:
    p = sub.add_parser("check", help="validate config, prompts, secrets and (optionally) endpoints")
    p.add_argument("--online", action="store_true", help="also contact provider endpoints and the SMTP server")
    p.set_defaults(func=cmd_check)


def _add_generate(sub) -> None:
    p = sub.add_parser("generate", help="write the Asterisk files, nftables ruleset and prompt list from the config")
    p.add_argument("--out", help="output directory (default: [paths].generated_dir)")
    p.set_defaults(func=cmd_generate)


def cmd_test_call(args) -> int:
    from .testcall import run
    cfg, _, _ = load_all(args)
    return run(cfg, line_id=args.line, digit=args.digit, target=args.target, wav=args.wav,
               fake_providers=args.fake_providers, timeout=args.timeout)


def _add_test_call(sub) -> None:
    p = sub.add_parser("test-call", help="place a local SIPp call and wait for the worker to deliver it")
    p.add_argument("--line", help="line id (default: the first line)")
    p.add_argument("--digit", help="menu key to press (default: 1 on menu lines)")
    p.add_argument("--target", help="SIP host:port (default: 127.0.0.1 and the [trunk].bind port)")
    p.add_argument("--wav", help="8 kHz mono 16-bit speech to send after the beep (up to 20 s)")
    p.add_argument("--fake-providers", action="store_true", help="also require the email in the fake-provider outbox")
    p.add_argument("--timeout", type=int, default=300, help="seconds to wait for the worker (default 300)")
    p.set_defaults(func=cmd_test_call)


COMMANDS = [_add_worker, _add_render_prompts, _add_check, _add_generate, _add_test_call]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aivoicemail", description=__doc__)
    p.add_argument("--version", action="version", version=f"aivoicemail {__version__}")
    p.add_argument("--config", default=os.environ.get("AIVOICEMAIL_CONFIG", "config/aivoicemail.toml"))
    p.add_argument("--env-file", default=os.environ.get("AIVOICEMAIL_ENV_FILE"),
                   help="secrets file (default: .env in the install root)")
    sub = p.add_subparsers(dest="command", required=True)
    for add in COMMANDS:
        add(sub)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as e:
        for problem in e.problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        return 2
