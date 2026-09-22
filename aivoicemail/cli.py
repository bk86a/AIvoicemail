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


COMMANDS = [_add_worker]


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
