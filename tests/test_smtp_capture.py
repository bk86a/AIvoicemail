import smtplib
from email.message import EmailMessage

from aivoicemail.fake.smtp_capture import CaptureServer, messages


def test_capture_stores_messages(tmp_path):
    server = CaptureServer(tmp_path / "outbox")
    port = server.start()
    try:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = "a@acme.example", "b@acme.example", "Grüße"
        msg.set_content("Zażółć gęślą jaźń\n.leading dot line\n")
        with smtplib.SMTP("127.0.0.1", port, timeout=10) as s:
            s.send_message(msg)
            s.send_message(msg)
    finally:
        server.stop()
    got = messages(tmp_path / "outbox")
    assert len(got) == 2
    assert got[0]["Subject"] == "Grüße"
    assert got[0].get_content() == "Zażółć gęślą jaźń\n.leading dot line\n"
