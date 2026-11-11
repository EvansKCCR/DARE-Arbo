"""SMTP delivery for assignment notices and meeting invitations."""
from email.utils import parseaddr
import smtplib
import ssl


def configured(config):
    return bool(config.get("from_address") and config.get("app_url") and config.get("password"))


class SMTPMailer:
    def __init__(self, config):
        self.config = dict(config or {})

    def _settings(self):
        if not configured(self.config):
            raise ValueError("Configure email.from_address, email.app_url and email.password in Streamlit Secrets.")
        sender = parseaddr(str(self.config["from_address"]))[1].strip().casefold()
        username = str(self.config.get("username") or sender).strip()
        host = str(self.config.get("smtp_host") or "smtp.gmail.com").strip()
        try:
            port = int(self.config.get("smtp_port") or 587)
        except (TypeError, ValueError):
            raise ValueError("email.smtp_port must be a valid port number.") from None
        if not sender or not username or not host or not 1 <= port <= 65535:
            raise ValueError("Check the SMTP sender address, username, host and port.")
        return sender, username, host, port, str(self.config["password"]).replace(" ", "")

    def verify_mail_sender(self, expected_address):
        sender, username, host, port, password = self._settings()
        if sender != str(expected_address or "").strip().casefold():
            raise PermissionError("The configured SMTP sender does not match the expected administrator address.")
        context = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=30) as client:
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
            client.login(username, password)
        return sender

    def send_message(self, message):
        sender, username, host, port, password = self._settings()
        actual = parseaddr(str(message.get("From", "")))[1].strip().casefold()
        if actual != sender:
            raise PermissionError("The message sender does not match the configured administrator address.")
        context = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=30) as client:
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
            client.login(username, password)
            client.send_message(message)
