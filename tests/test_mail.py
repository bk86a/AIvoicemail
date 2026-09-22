import smtplib

import pytest

from aivoicemail.config import Mail
from aivoicemail.fake.smtp_capture import CaptureServer, messages
from aivoicemail.mail import Mailer, SendError

CFG = Mail(smtp_host="smtp.example", smtp_port=587, from_address="voicemail@acme.example", from_name="ACME Voicemail",
           alert_to="admin@acme.example", smtp_security="starttls", smtp_user_env="SMTP_USER", smtp_password_env="SMTP_PASSWORD")
ENV = {"SMTP_USER": "u", "SMTP_PASSWORD": "p"}


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout, context=None):
        self.args, self.calls, self.refuse = (host, port, timeout, context), [], {}
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.calls.append("quit")

    def starttls(self, context):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, msg):
        self.calls.append(("send", msg["To"]))
        return self.refuse


@pytest.fixture(autouse=True)
def reset():
    FakeSMTP.instances.clear()


def test_starttls_login_send_returns_message_id():
    m = Mailer(CFG, ENV, smtp=FakeSMTP, smtp_ssl=None)
    mid = m.send(to="info@acme.example", subject="S", text="T")
    s = FakeSMTP.instances[0]
    assert s.args[:3] == ("smtp.example", 587, 60)
    assert s.calls == ["starttls", ("login", "u", "p"), ("send", "info@acme.example"), "quit"]
    assert mid.startswith("<") and mid.endswith("@acme.example>")


def test_implicit_tls_uses_smtp_ssl():
    cfg = Mail(**{**CFG.__dict__, "smtp_port": 465, "smtp_security": "tls"})
    Mailer(cfg, ENV, smtp=None, smtp_ssl=FakeSMTP).send(to="info@acme.example", subject="S", text="T")
    s = FakeSMTP.instances[0]
    assert s.args[1] == 465 and s.args[3] is not None and "starttls" not in s.calls


def test_missing_credentials_fail_before_connecting():
    with pytest.raises(SendError):
        Mailer(CFG, {}, smtp=FakeSMTP).send(to="info@acme.example", subject="S", text="T")
    assert FakeSMTP.instances == []


def test_refused_recipient_is_failure():
    class Refusing(FakeSMTP):
        def send_message(self, msg):
            return {"info@acme.example": (550, b"no")}

    with pytest.raises(SendError):
        Mailer(CFG, ENV, smtp=Refusing).send(to="info@acme.example", subject="S", text="T")


def test_smtp_exception_becomes_send_error():
    class Broken(FakeSMTP):
        def starttls(self, context):
            raise smtplib.SMTPException("tls failed")

    with pytest.raises(SendError) as e:
        Mailer(CFG, ENV, smtp=Broken).send(to="info@acme.example", subject="S", text="T")
    assert "SMTPException" in str(e.value)


def test_connection_error_becomes_send_error():
    def refuse(*a, **k):
        raise ConnectionRefusedError("refused")

    with pytest.raises(SendError):
        Mailer(CFG, ENV, smtp=refuse).send(to="info@acme.example", subject="S", text="T")


def test_real_smtp_roundtrip_with_attachment(tmp_path):
    server = CaptureServer(tmp_path / "outbox")
    port = server.start()
    try:
        cfg = Mail(**{**CFG.__dict__, "smtp_host": "127.0.0.1", "smtp_port": port, "smtp_security": "none",
                      "smtp_user_env": None, "smtp_password_env": None})
        mid = Mailer(cfg, {}).send(to="info@acme.example", subject="[Voicemail] Test ü", text="Body é",
                                   attachments=[("x.wav", b"RIFFdata", "audio/wav")])
    finally:
        server.stop()
    [msg] = messages(tmp_path / "outbox")
    assert msg["Message-ID"] == mid and msg["Subject"] == "[Voicemail] Test ü"
    assert msg["From"] == "ACME Voicemail <voicemail@acme.example>" and msg["Auto-Submitted"] == "auto-generated"
    att = next(msg.iter_attachments())
    assert att.get_filename() == "x.wav" and att.get_content_type() == "audio/wav" and att.get_content() == b"RIFFdata"
