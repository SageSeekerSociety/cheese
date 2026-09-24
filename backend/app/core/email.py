import logging
from collections.abc import Sequence
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

from app.core.config import settings

logger = logging.getLogger(__name__)

# Domains the platform once wrote into accounts that had no address of their
# own. Nothing can receive mail there, and an account holding one has no
# address it can be recovered through.
PLACEHOLDER_EMAIL_DOMAINS = frozenset({"placeholder.internal", "oauth.ruc.local"})


def is_placeholder_email(email: str | None) -> bool:
    if not email or "@" not in email:
        return False
    return email.rsplit("@", 1)[1].strip().lower() in PLACEHOLDER_EMAIL_DOMAINS


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

    @property
    def is_configured(self) -> bool:
        return bool(self._host and self._from_address)

    async def send(
        self,
        *,
        to: str | Sequence[str],
        subject: str,
        body_html: str,
        body_text: str | None = None,
    ) -> bool:
        if not self.is_configured:
            logger.warning("Email not configured, skipping send to %s", to)
            return False

        recipients = [to] if isinstance(to, str) else list(to)
        # Every mail leaves through here, so this is the one place that keeps
        # a placeholder address from ever being handed to the SMTP server.
        dropped = [r for r in recipients if is_placeholder_email(r)]
        if dropped:
            logger.warning("Not sending to placeholder addresses %s", dropped)
            recipients = [r for r in recipients if r not in dropped]
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


class FallbackEmailSender:
    """Send through the primary account; if that fails, through the fallback.

    The fallback exists because the primary is a free-plan mailbox with a daily
    recipient cap: a verification code that cannot go out blocks a signup, so
    one more attempt through another account is worth a second From address.
    """

    def __init__(self, primary: EmailSender, fallback: EmailSender) -> None:
        self._primary = primary
        self._fallback = fallback

    @property
    def is_configured(self) -> bool:
        return self._primary.is_configured or self._fallback.is_configured

    async def send(
        self,
        *,
        to: str | Sequence[str],
        subject: str,
        body_html: str,
        body_text: str | None = None,
    ) -> bool:
        message = {
            "to": to,
            "subject": subject,
            "body_html": body_html,
            "body_text": body_text,
        }
        if await self._primary.send(**message):
            return True
        logger.warning("Primary SMTP failed for %s; trying the fallback", to)
        return await self._fallback.send(**message)


def get_email_sender() -> EmailSender | FallbackEmailSender:
    primary = EmailSender()
    if not settings.email_fallback_smtp_host:
        return primary
    return FallbackEmailSender(
        primary,
        EmailSender(
            host=settings.email_fallback_smtp_host,
            port=settings.email_fallback_smtp_port,
            username=settings.email_fallback_smtp_username,
            password=settings.email_fallback_smtp_password,
            from_address=settings.email_fallback_from_address,
            use_ssl=settings.email_fallback_smtp_ssl,
        ),
    )
