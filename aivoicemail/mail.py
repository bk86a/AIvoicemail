"""SMTP delivery with STARTTLS or implicit TLS. Success = the server accepted the message."""
import smtplib
import ssl
from email.message import EmailMessage

from email.utils import formataddr, formatdate, make_msgid

from .config import secret


class SendError(Exception):
    pass


class Mailer:
    def __init__(self, cfg, env, *, timeout=60, smtp=smtplib.SMTP, smtp_ssl=smtplib.SMTP_SSL):
        self.cfg, self.env, self.timeout = cfg, env, timeout
        self._smtp, self._smtp_ssl = smtp, smtp_ssl

    def build(self, *, to, subject, text, attachments=()) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = formataddr((self.cfg.from_name, self.cfg.from_address))
        msg["To"] = to
        msg["Subject"] = subject
        msg["Date"] = formatdate(usegmt=True)
        msg["Message-ID"] = make_msgid(domain=self.cfg.from_address.rpartition("@")[2] or None)
        msg["Auto-Submitted"] = "auto-generated"
        msg.set_content(text)
        for name, content, mime in attachments:
            maintype, _, subtype = mime.partition("/")
            msg.add_attachment(content, maintype=maintype, subtype=subtype or "octet-stream", filename=name)
        return msg

    def send(self, *, to, subject, text, attachments=()) -> str:
        msg = self.build(to=to, subject=subject, text=text, attachments=attachments)
        user = secret(self.env, self.cfg.smtp_user_env)
        password = secret(self.env, self.cfg.smtp_password_env)
        if self.cfg.smtp_user_env and not (user and password):
            raise SendError("SMTP credentials missing")
        try:
            if self.cfg.smtp_security == "tls":
                conn = self._smtp_ssl(self.cfg.smtp_host, self.cfg.smtp_port, timeout=self.timeout,
                                      context=ssl.create_default_context())
            else:
                conn = self._smtp(self.cfg.smtp_host, self.cfg.smtp_port, timeout=self.timeout)
            with conn:
                if self.cfg.smtp_security == "starttls":
                    conn.starttls(context=ssl.create_default_context())
                if user:
                    conn.login(user, password)
                refused = conn.send_message(msg)
        except (smtplib.SMTPException, OSError) as e:
            raise SendError(f"{type(e).__name__}: {str(e)[:200]}") from None
        if refused:
            raise SendError("recipient refused")
        return msg["Message-ID"]
