"""Per-item state machine: speech check -> STT chain -> LLM chain -> email -> ack -> call log.

Failure semantics (spec section 4):
- send failure: no ack; the rendered email is kept and resent next cycle (no second provider run);
- ack failure: only the ack is retried (no duplicate email);
- `max_failures` consecutive unexpected errors: fallback email (WAV if present) + alert, then ack.
Log lines carry item IDs, line ids and outcomes only - never caller numbers or content.
"""
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import render
from .mail import SendError
from .spool.base import SpoolError


@dataclass
class Deps:
    cfg: Any
    spool: Any
    speech: Any
    transcribe: Callable
    summarise: Callable
    send: Callable
    calllog: Any
    alerter: Any = None
    log: Callable = print
    clock: Callable = time.time


@dataclass(frozen=True)
class Pending:
    meta: dict
    outcome: str
    to: str
    email: render.Email
    attachments: tuple = ()
    providers: dict | None = None


class Processor:
    def __init__(self, deps: Deps):
        self.d = deps
        self.sent_pending_ack: dict[str, tuple[dict, str]] = {}
        self.pending_send: dict[str, Pending] = {}
        self.failures: dict[str, int] = {}
        self.giveup_alert_pending: set[str] = set()

    def process(self, item) -> str:
        if item.id in self.sent_pending_ack:
            return self._retry_saved(item.id, lambda: self._ack(item.id, retry=True))
        if item.id in self.pending_send:
            return self._retry_saved(item.id, lambda: self._deliver(item.id, self.pending_send[item.id]))
        work = Path(self.d.cfg.paths.work_dir) / item.id
        meta = wav = None
        try:
            meta, wav = self.d.spool.get(item.id, work)
            result = self._pipeline(item.id, meta, wav)
            self.failures.pop(item.id, None)
            return result
        except SpoolError as e:
            self.d.log(f"{item.id}: spool error: {e}")
            return "retry"
        except Exception as e:
            count = self._bump_failures(item.id, e)
            if count < self.d.cfg.worker.max_failures:
                return "retry"
            return self._give_up(item.id, meta, wav)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _bump_failures(self, item_id, exc) -> int:
        count = self.failures[item_id] = self.failures.get(item_id, 0) + 1
        self.d.log(f"{item_id}: processing failed ({type(exc).__name__}), attempt {count}/{self.d.cfg.worker.max_failures}")
        return count

    def _retry_saved(self, item_id, fn) -> str:
        """Re-run a cached ack-only or send-only retry under the same failure accounting as a fresh
        attempt, so an unexpected (non-SpoolError/SendError) exception from it can't retry forever
        without ever reaching the give-up threshold."""
        try:
            result = fn()
        except SpoolError as e:
            self.d.log(f"{item_id}: spool error: {e}")
            return "retry"
        except Exception as e:
            pending = self.pending_send.get(item_id)
            if pending is not None:
                meta, attachments = pending.meta, pending.attachments
            else:
                saved = self.sent_pending_ack.get(item_id)
                meta, attachments = (saved[0] if saved is not None else {"id": item_id}), ()
            count = self._bump_failures(item_id, e)
            if count < self.d.cfg.worker.max_failures:
                return "retry"
            self.pending_send.pop(item_id, None)
            self.sent_pending_ack.pop(item_id, None)
            return self._give_up(item_id, meta, None, attachments=attachments)
        self.failures.pop(item_id, None)
        return result

    def _pipeline(self, item_id, meta, wav) -> str:
        line = self.d.cfg.line(meta.get("line"))
        if line is None:
            raise ValueError("unknown line")
        attachment = ((wav.name, wav.read_bytes(), "audio/wav"),) if wav is not None else ()
        speech = 0.0
        if wav is not None:
            try:
                speech = self.d.speech.speech_seconds(wav)
            except Exception as e:
                self.d.log(f"{item_id}: speech detection failed: {type(e).__name__}")
                return self._deliver(item_id, Pending(meta, "fallback-speech-detection", line.mailbox,
                                                      render.fallback(line, meta, None, "speech detection failed"),
                                                      attachment))
        if wav is None or speech < self.d.cfg.recording.min_speech_seconds:
            return self._deliver(item_id, Pending(meta, "missed", line.mailbox, render.missed(line, meta)))
        t = self.d.transcribe(wav, meta["lang_choice"], line.menu)
        if t is None:
            return self._deliver(item_id, Pending(meta, "fallback-transcription", line.mailbox,
                                                  render.fallback(line, meta, None, "transcription failed"), attachment))
        transcript, t_provider = t
        s = self.d.summarise(transcript, meta, line.email_language)
        if s is None:
            return self._deliver(item_id, Pending(meta, "fallback-summary", line.mailbox,
                                                  render.fallback(line, meta, transcript, "summary failed"),
                                                  attachment, {"transcript": t_provider}))
        summary, s_provider = s
        providers = {"transcript": t_provider, "summary": s_provider}
        return self._deliver(item_id, Pending(meta, "message", line.mailbox,
                                              render.message(line, meta, transcript, summary, providers), (), providers))

    def _log_call(self, meta, outcome, *, providers=None, message_id=None) -> None:
        self.d.calllog.record(meta, outcome, providers=providers, message_id=message_id)
        self.d.log(f"{meta.get('id')} {meta.get('line')} {outcome} msg={message_id}")

    def _deliver(self, item_id, pending: Pending) -> str:
        first_attempt = item_id not in self.pending_send
        self.pending_send[item_id] = pending
        try:
            message_id = self.d.send(to=pending.to, subject=pending.email.subject, text=pending.email.text,
                                     attachments=pending.attachments)
        except SendError as e:
            self.d.log(f"{item_id}: send failed, will retry: {e}")
            if first_attempt:
                self._log_call(pending.meta, "send-failed", providers=pending.providers)
            return "retry"
        self.pending_send.pop(item_id, None)
        self._log_call(pending.meta, pending.outcome, providers=pending.providers, message_id=message_id)
        self.sent_pending_ack[item_id] = (pending.meta, message_id)
        if item_id in self.giveup_alert_pending:
            self.giveup_alert_pending.discard(item_id)
            self._alert_giveup(item_id)
        return self._ack(item_id)

    def _alert_giveup(self, item_id) -> None:
        """Best-effort operator alert. The ack above has already been recorded in
        sent_pending_ack, so a missing alerter or an alert failure here must never block it."""
        if self.d.alerter is None:
            return
        try:
            self.d.alerter.notify(
                "processing failed",
                f"Item {item_id} failed processing {self.d.cfg.worker.max_failures} times in a row; it was sent "
                "to the mailbox as a fallback email and removed from the spool.", self.d.clock())
        except Exception as e:
            self.d.log(f"{item_id}: give-up alert failed: {type(e).__name__}: {e}")

    def _ack(self, item_id, *, retry=False) -> str:
        try:
            self.d.spool.ack(item_id)
        except SpoolError as e:
            self.d.log(f"{item_id}: ack failed, will retry ack only: {e}")
            return "retry"
        meta, message_id = self.sent_pending_ack.pop(item_id, ({"id": item_id}, None))
        if retry:
            self._log_call(meta, "acked-after-retry", message_id=message_id)
        return "acked"

    def _give_up(self, item_id, meta, wav, *, attachments=None) -> str:
        """Poison item: send what we have to the item's line mailbox (else the first line), alert, then ack.

        `attachments`, when given (e.g. by _retry_saved, resuming a previously-rendered Pending),
        is used as-is instead of re-derived from `wav` - the pipeline already materialised it and
        the on-disk work directory may be long gone. Otherwise it is built from `wav`, falling back
        to the item's own work directory in case a wav was already fetched there before the error
        that triggered give-up (e.g. get() failed after downloading audio but before parsing meta)."""
        meta = meta if isinstance(meta, dict) else {}
        line = self.d.cfg.line(meta.get("line")) or self.d.cfg.lines[0]
        safe = {"id": item_id}
        for key in ("line", "did", "caller", "started_at", "duration_s"):
            safe[key] = " ".join(str(meta.get(key, "?")).split())[:64] or "?"
        if attachments is None:
            if wav is None:
                candidate = Path(self.d.cfg.paths.work_dir) / item_id / f"{item_id}.wav"
                wav = candidate if candidate.is_file() else None
            attachments = ((Path(wav).name, Path(wav).read_bytes(), "audio/wav"),) \
                if wav is not None and Path(wav).is_file() else ()
        self.pending_send.pop(item_id, None)
        self.giveup_alert_pending.add(item_id)
        self.failures.pop(item_id, None)
        pending = Pending(safe, "fallback-failed-repeatedly", line.mailbox,
                          render.fallback(line, safe, None, "processing failed repeatedly"), attachments)
        try:
            return self._deliver(item_id, pending)
        except Exception as e:
            # Safety net only: _deliver already handles SendError itself. Anything else escaping
            # it here (e.g. the fallback send raising a non-SendError error too) must not crash
            # process() - leave it to be retried like any other pending send.
            self.d.log(f"{item_id}: give-up delivery failed unexpectedly, will retry: {type(e).__name__}: {e}")
            return "retry"
