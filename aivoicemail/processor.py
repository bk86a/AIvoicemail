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
        self.send_failed_logged: set[str] = set()

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
            # A delivered item (already sitting in sent_pending_ack) must never reach give-up, no
            # matter how this exception got here - checked fresh, at the moment of the exception,
            # not from a flag computed before the pipeline ran (it may have just been delivered).
            if item.id in self.sent_pending_ack:
                return self._ack_failed(item.id, e)
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

    def _ack_failed(self, item_id, exc) -> str:
        """The email for this item was already delivered - an ack failure (of any kind) must never
        cause a resend. Keep retrying the ack forever; alert the operator once (no personal data)
        if it reaches max_failures."""
        count = self.failures[item_id] = self.failures.get(item_id, 0) + 1
        limit = self.d.cfg.worker.max_failures
        self.d.log(f"{item_id}: ack failed, will retry ack only ({type(exc).__name__}), attempt {count}/{limit}")
        if count == limit:
            self._alert_ack_failing(item_id)
        return "retry"

    def _retry_saved(self, item_id, fn) -> str:
        """Re-run a cached ack-only or send-only retry under the same failure accounting as a fresh
        attempt, so an unexpected (non-SpoolError/SendError) exception from it can't retry forever
        without ever reaching the give-up threshold.

        The ack-only case (item_id already in sent_pending_ack, checked fresh at exception time, not
        from a flag computed before fn() ran) is different: the email was already delivered
        successfully, so it must NEVER be resent - not even via give-up's fallback email."""
        try:
            result = fn()
        except SpoolError as e:
            self.d.log(f"{item_id}: spool error: {e}")
            return "retry"
        except Exception as e:
            if item_id in self.sent_pending_ack:
                return self._ack_failed(item_id, e)
            count = self._bump_failures(item_id, e)
            if count < self.d.cfg.worker.max_failures:
                return "retry"
            pending = self.pending_send.pop(item_id, None)
            meta = pending.meta if pending is not None else {"id": item_id}
            attachments = pending.attachments if pending is not None else ()
            return self._give_up(item_id, meta, None, attachments=attachments)
        # Only a genuine success resets the counter - a "retry" here means fn() itself already
        # turned a SendError/SpoolError into a handled retry, which is not progress.
        if result == "acked":
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
        """Best effort: CallLog handles OSError itself, but nothing raised here may turn a delivered
        email into a failure (and so into a resend)."""
        try:
            self.d.calllog.record(meta, outcome, providers=providers, message_id=message_id)
        except Exception as e:
            self.d.log(f"{meta.get('id')}: call log failed ({type(e).__name__})")
        self.d.log(f"{meta.get('id')} {meta.get('line')} {outcome} msg={message_id}")

    def evict(self, keep_ids) -> None:
        """Forget every item the spool no longer lists (acked elsewhere, removed by the operator), so
        cached emails and WAV bytes do not accumulate. Only call it after a successful list()."""
        keep = set(keep_ids)
        for state in (self.sent_pending_ack, self.pending_send, self.failures):
            for item_id in [i for i in state if i not in keep]:
                del state[item_id]
        self.send_failed_logged &= keep
        self.giveup_alert_pending &= keep

    def _deliver(self, item_id, pending: Pending) -> str:
        self.pending_send[item_id] = pending
        try:
            message_id = self.d.send(to=pending.to, subject=pending.email.subject, text=pending.email.text,
                                     attachments=pending.attachments)
        except SendError as e:
            self.d.log(f"{item_id}: send failed, will retry: {e}")
            # Logged once per item, ever - not once per Pending, so a later give-up fallback email
            # hitting its own SendError doesn't add a second "send-failed" call-log entry.
            if item_id not in self.send_failed_logged:
                self.send_failed_logged.add(item_id)
                self._log_call(pending.meta, "send-failed", providers=pending.providers)
            return "retry"
        self.pending_send.pop(item_id, None)
        # Recorded first: from here on the item may only ever be acked, never resent.
        self.sent_pending_ack[item_id] = (pending.meta, message_id)
        self._log_call(pending.meta, pending.outcome, providers=pending.providers, message_id=message_id)
        # The email is now delivered - any pre-delivery pipeline/send failure count must not leak
        # into ack-only retries, which are a different failure mode with their own accounting.
        self.failures.pop(item_id, None)
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
            self.d.log(f"{item_id}: give-up alert failed: {type(e).__name__}")

    def _alert_ack_failing(self, item_id) -> None:
        """Best-effort operator alert: the email was already delivered but acking (removing the
        item from the spool) keeps failing. Never resends anything - it only informs the operator
        once; failures here are logged, never raised."""
        if self.d.alerter is None:
            return
        try:
            self.d.alerter.notify(
                "ack failing",
                f"Item {item_id} could not be acknowledged (removed from the spool) after "
                f"{self.d.cfg.worker.max_failures} attempts, although its email was already "
                "delivered; it will keep being retried.", self.d.clock())
        except Exception as e:
            self.d.log(f"{item_id}: ack-failing alert failed: {type(e).__name__}")

    def _ack(self, item_id, *, retry=False) -> str:
        try:
            self.d.spool.ack(item_id)
        except Exception as e:
            # Any exception here (not just SpoolError) is an ack failure, never a send failure: the
            # email was already delivered, so this must never escalate to give-up's resend.
            return self._ack_failed(item_id, e)
        meta, message_id = self.sent_pending_ack.pop(item_id, ({"id": item_id}, None))
        self.send_failed_logged.discard(item_id)
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
        # Reset first, before anything that can raise: a failed give-up must not leave the counter at
        # the limit, or every following cycle would give up again. The next attempt counts from 1.
        self.failures.pop(item_id, None)
        self.pending_send.pop(item_id, None)
        try:
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
            pending = Pending(safe, "fallback-failed-repeatedly", line.mailbox,
                              render.fallback(line, safe, None, "processing failed repeatedly"), attachments)
            self.giveup_alert_pending.add(item_id)
            return self._deliver(item_id, pending)
        except Exception as e:
            # _deliver handles SendError itself. Anything else (reading the WAV, rendering, a
            # non-SendError from the fallback send) must not crash process(): the item stays in the
            # spool and is retried from scratch, reaching give-up again only after max_failures more.
            self.d.log(f"{item_id}: give-up delivery failed unexpectedly, will retry: {type(e).__name__}")
            return "retry"
