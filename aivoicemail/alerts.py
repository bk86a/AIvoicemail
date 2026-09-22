"""Operator alerts without personal data, at most one per condition per hour."""
from . import render
from .mail import SendError

ALERT_REPEAT_SECONDS = 3600


class Alerter:
    def __init__(self, *, send, alert_to, stale_minutes=60, unreachable_minutes=15, log=print):
        self.send, self.alert_to, self.log = send, alert_to, log
        self.stale_minutes, self.unreachable_minutes = stale_minutes, unreachable_minutes
        self.last_sent: dict[str, float] = {}

    def notify(self, kind, detail, now) -> bool:
        last = self.last_sent.get(kind)
        if last is not None and now - last < ALERT_REPEAT_SECONDS:
            return False
        email = render.alert(kind, detail)
        try:
            self.send(to=self.alert_to, subject=email.subject, text=email.text, attachments=())
        except SendError as e:
            self.log(f"alert {kind!r} not sent: {e}")
            return False
        self.last_sent[kind] = now
        return True

    def check(self, waiting, *, last_list_ok, now, spool_label, orphans=0) -> None:
        stale = [i for i in waiting if now - i.mtime > self.stale_minutes * 60]
        if stale:
            self.notify("stale", f"{len(stale)} item(s) waiting longer than {self.stale_minutes} min.", now)
        if now - last_list_ok > self.unreachable_minutes * 60:
            self.notify("spool unreachable",
                        f"Spool not reachable for over {self.unreachable_minutes} min ({spool_label}).", now)
        if orphans:
            self.notify("stale orphan",
                        f"{orphans} orphaned audio or temporary file(s) older than 60 min in the spool "
                        "(tmp/, or ready/ without metadata). They were not deleted; check the telephony host.",
                        now)
