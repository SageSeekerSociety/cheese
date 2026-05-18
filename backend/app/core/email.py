import logging
from collections.abc import Sequence
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

from app.core.config import settings

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
        use_ssl: bool | None = None,
        use_tls: bool = True,
    ) -> None:
        self._host = host or settings.email_smtp_host
        self._port = port or settings.email_smtp_port
        self._username = username or settings.email_smtp_username
        self._password = password or settings.email_smtp_password
        self._from_address = from_address or settings.email_from_address
        self._use_ssl = use_ssl if use_ssl is not None else settings.email_smtp_ssl
        self._use_tls = use_tls

    async def send(
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
            await aiosmtplib.send(
                msg,
                hostname=self._host,
                port=self._port,
                username=self._username if self._username and self._password else None,
                password=self._password if self._username and self._password else None,
                use_tls=self._use_ssl or False,
                start_tls=self._use_tls and not self._use_ssl,
                timeout=30,
            )
            logger.debug("Email sent to %s: %s", recipients, subject)
            return True
        except Exception:
            logger.exception("Failed to send email to %s", recipients)
            return False


def get_email_sender() -> EmailSender:
    return EmailSender()
