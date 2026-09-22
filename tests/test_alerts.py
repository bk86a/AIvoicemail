from aivoicemail.alerts import Alerter
from aivoicemail.mail import SendError
from aivoicemail.spool import Item

ITEM = Item("0f8fad5b-d9cb-469f-a165-70867728950e", 0, True)


def make(sent, fail=False):
    def send(**kw):
        if fail:
            raise SendError("down")
        sent.append(kw)
        return "<m>"
    return Alerter(send=send, alert_to="admin@acme.example", log=lambda *a: None)


def test_stale_alert_once_per_hour():
    sent = []
    a = make(sent)
    for now in (4000, 4100, 4000 + 3601):
        a.check([ITEM], last_list_ok=now, now=now, spool_label="directory missing or unreadable")
    assert [s["subject"] for s in sent] == ["[Voicemail alert] stale"] * 2
    assert sent[0]["to"] == "admin@acme.example" and sent[0]["attachments"] == ()
    assert sent[0]["text"] == "1 item(s) waiting longer than 60 min.\n"


def test_fresh_items_do_not_alert():
    sent = []
    make(sent).check([Item(ITEM.id, 1000, True)], last_list_ok=1000 + 59 * 60, now=1000 + 59 * 60, spool_label="x")
    assert sent == []


def test_unreachable_alert_names_backend_only():
    sent = []
    make(sent).check([], last_list_ok=0, now=16 * 60, spool_label="SSH failure")
    assert sent[0]["subject"] == "[Voicemail alert] spool unreachable"
    assert sent[0]["text"] == "Spool not reachable for over 15 min (SSH failure).\n"


def test_failed_alert_send_is_retried_next_time():
    sent = []
    a = make(sent, fail=True)
    assert a.notify("stale", "x", 100) is False
    assert a.last_sent == {}
