"""Worker service: poll the spool every poll_seconds, process items, alert, prune the call log."""
import dataclasses
import functools
import time
from pathlib import Path

from . import llm, stt
from .alerts import Alerter
from .calllog import CallLog
from .config import LOCAL_STT
from .mail import Mailer
from .processor import Deps
from .spool import SpoolError, build_spool


def build(cfg, env, *, fake=False, log=print):
    closers = []
    data = Path(cfg.paths.data_dir)
    mail_cfg = cfg.mail
    if fake:
        from .fake.providers import FakeSpeech, fake_summarise, fake_transcribe
        from .fake.smtp_capture import CaptureServer
        capture = CaptureServer(data / "outbox")
        port = capture.start()
        closers.append(capture.stop)
        mail_cfg = dataclasses.replace(cfg.mail, smtp_host="127.0.0.1", smtp_port=port, smtp_security="none",
                                       smtp_user_env=None, smtp_password_env=None)
        speech, transcribe, summarise = FakeSpeech(), fake_transcribe, fake_summarise
        log(f"fake providers mode: no audio or text leaves this host; emails are stored in {capture.directory}")
    else:
        from .stt.whisper_local import WhisperLocal
        speech = WhisperLocal(cfg.stt.whisper_model, cfg.stt.whisper_threads, data / "models")
        whisper = speech if LOCAL_STT in cfg.stt.chain else None
        transcribe = functools.partial(stt.transcribe, cfg=cfg.stt, env=env, whisper=whisper, log=log)

        def summarise(transcript, meta, email_language):
            return llm.summarise(transcript, meta, email_language=email_language, cfg=cfg.llm, env=env, log=log)
    mailer = Mailer(mail_cfg, env)
    deps = Deps(cfg=cfg, spool=build_spool(cfg), speech=speech, transcribe=transcribe, summarise=summarise,
                send=mailer.send, calllog=CallLog(data / "calllog", cfg.retention.call_log_days, log=log), log=log)
    deps.alerter = Alerter(send=mailer.send, alert_to=cfg.mail.alert_to, stale_minutes=cfg.worker.stale_minutes,
                           unreachable_minutes=cfg.worker.unreachable_minutes, log=log)
    return deps, closers


def run(deps, processor, *, once=False, sleep=time.sleep):
    label = "SSH failure" if deps.cfg.spool.backend == "ssh" else "directory missing or unreadable"
    last_ok = deps.clock()
    while True:
        items = []
        try:
            items = deps.spool.list()
            last_ok = deps.clock()
        except SpoolError as e:
            deps.log(f"list failed: {e}")
        waiting = []  # items still in the spool after this cycle; acked ones cannot be stale
        for item in items:
            try:
                if processor.process(item) != "acked":
                    waiting.append(item)
            except Exception as e:  # never let one item stop the loop
                deps.log(f"{item.id}: unexpected {type(e).__name__}")
                waiting.append(item)
        deps.alerter.check(waiting, last_list_ok=last_ok, now=deps.clock(), spool_label=label)
        deps.calllog.prune()
        if once:
            return
        sleep(deps.cfg.worker.poll_seconds)
