"""aivoicemail test-call: a local SIPp call into the running Asterisk, then wait for the worker's outcome.

Runs where Asterisk runs (single host, "tools" service with host networking). The call comes from
127.0.0.1, which the generated trunk identifies when [trunk].allow_local_test = true (default).
Menu keys are sent as in-band DTMF, the path carriers without telephone-event use."""
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .calllog import read_entries
from .sipp import pcap, timings

DELIVERED = ("message", "missed", "fallback-speech-detection", "fallback-transcription", "fallback-summary",
             "fallback-failed-repeatedly", "acked-after-retry")


def plan_call(line, digit, d) -> tuple[str, int]:
    if line.has_menu and digit:
        return "inband", timings.dtmf_post_ms(d, line.id)
    if line.has_menu:
        return "plain", timings.timeout_ms(d, line.id)
    return "plain", timings.single_ms(d, line.id)


def speech_lead(line, digit, d) -> float:
    """Seconds from the start of the speech clip until the recording beep has played."""
    if line.has_menu and digit:
        return d[f"{line.id}-notice-{line.menu[int(digit) - 1]}"] + timings.BEEP_S
    if line.has_menu:
        return (timings.WAIT_S + d[f"{line.id}-menu"] + timings.EXTEN_S + d[f"{line.id}-notice-auto"]
                + timings.BEEP_S)
    return timings.WAIT_S + d[f"{line.id}-notice-{line.menu[0]}"] + timings.BEEP_S


def sipp_command(line, scenario, *, target) -> list[str]:
    return ["sipp", target, "-sf", str(scenario), "-s", line.did, "-i", "127.0.0.1", "-p", "15070",
            "-mi", "127.0.0.1", "-min_rtp_port", "26000", "-max_rtp_port", "26100", "-m", "1",
            "-timeout", "200s", "-timeout_error", "-nostdin"]


def _ts(value) -> float:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def wait_for_outcome(calllog_dir, did, since, *, timeout, clock=time.time, sleep=time.sleep):
    deadline = clock() + timeout
    while True:
        entries = read_entries(calllog_dir) if Path(calllog_dir).is_dir() else []
        for entry in reversed(entries):
            if (entry.get("did") == did and entry.get("outcome") in DELIVERED
                    and _ts(entry["logged_at"]) >= int(since) - 1):
                return entry
        if clock() >= deadline:
            return None
        sleep(5)


def outbox_email(outbox, item_id):
    for path in sorted(Path(outbox).glob("*.eml")):
        if item_id in path.read_text(encoding="utf-8", errors="replace"):
            return path
    return None


def run(cfg, *, line_id=None, digit=None, target=None, wav=None, fake_providers=False, timeout=300,
        runner=subprocess.run, clock=time.time, sleep=time.sleep, out=print) -> int:
    line = cfg.line(line_id) if line_id else cfg.lines[0]
    if line is None:
        out(f"ERROR: no line {line_id!r} in the config")
        return 2
    if line.has_menu and digit is None:
        digit = "1"
    if digit is not None and (not line.has_menu or digit not in [str(i) for i in range(1, len(line.menu) + 1)]):
        out(f"ERROR: line {line.id} has no menu key {digit!r}")
        return 2
    d = timings.durations(Path(cfg.paths.generated_dir) / "sounds" / "vm")
    if not d:
        out("ERROR: no rendered prompts - run render-prompts first")
        return 2
    mode, pause_ms = plan_call(line, digit, d)
    target = target or f"127.0.0.1:{cfg.trunk.sip_port}"
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        pcap.write_media(work, digits=digit or "", wav=wav, lead=speech_lead(line, digit, d) if wav else 0.0)
        scenario = work / "call.xml"
        text = timings.render_call(mode, pause_ms, str(work))
        if digit:
            text = text.replace("[field0]", digit)
        scenario.write_text(text, encoding="utf-8")
        out(f"test-call: line {line.id} ({line.did}) via {target}, "
            f"{'menu key ' + digit if digit else 'no key'}, about {pause_ms // 1000 + 4} s")
        started = clock()
        r = runner(sipp_command(line, scenario, target=target), capture_output=True, text=True)
    if r.returncode != 0:
        out(f"ERROR: SIPp failed (exit {r.returncode}): {(r.stderr or '').strip()[-500:]}")
        return 1
    entry = wait_for_outcome(Path(cfg.paths.data_dir) / "calllog", line.did, started, timeout=timeout,
                             clock=clock, sleep=sleep)
    if entry is None:
        out(f"ERROR: no delivered call for {line.did} within {timeout} s - check `docker compose logs worker`")
        return 1
    out(f"test-call: outcome={entry['outcome']} message_id={entry.get('message_id')} item={entry['id']}")
    if fake_providers:
        path = outbox_email(Path(cfg.paths.data_dir) / "outbox", entry["id"])
        if path is None:
            out("ERROR: no email for this call in the fake-provider outbox")
            return 1
        out(f"test-call: email stored in {path}")
    return 0
