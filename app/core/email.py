import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import TYPE_CHECKING

from app.core.config import settings

if TYPE_CHECKING:
    from collections.abc import Sequence


logger = logging.getLogger(__name__)


class EmailSender:
    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        username: str | None = None,
        password: str | None = None,
        from_address: str | None = None,
        use_tls: bool = True,
    ) -> None:
        self._host = host or settings.email_smtp_host
        self._port = port or settings.email_smtp_port
        self._username = username or settings.email_smtp_username
        self._password = password or settings.email_smtp_password
        self._from_address = from_address or settings.email_from_address
        self._use_tls = use_tls

    def send(
        self,
        *,
        to: str | Sequence[str],
        subject: str,
        body_html: str,
        body_text: str | None = None,
    ) -> bool:
        if not self._host or not self._from_address:
            logger.warning("Email not configured, skipping send to %s", to)
            return False

        recipients = [to] if isinstance(to, str) else list(to)
        if not recipients:
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self._from_address
        msg["To"] = ", ".join(recipients)

        if body_text:
            msg.attach(MIMEText(body_text, "plain", "utf-8"))
        msg.attach(MIMEText(body_html, "html", "utf-8"))

        try:
            with smtplib.SMTP(self._host, self._port, timeout=30) as server:
                if self._use_tls:
                    server.starttls()
                if self._username and self._password:
                    server.login(self._username, self._password)
                server.sendmail(self._from_address, recipients, msg.as_string())
            logger.debug("Email sent to %s: %s", recipients, subject)
            return True
        except Exception:
            logger.exception("Failed to send email to %s", recipients)
            return False


def get_email_sender() -> EmailSender:
    return EmailSender()
