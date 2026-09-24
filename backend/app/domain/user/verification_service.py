import logging
import secrets
import string
from dataclasses import dataclass
from enum import StrEnum

from redis.asyncio import Redis

from app.core.email import get_email_sender
from app.core.errors import BadRequestError, SystemBusyError
from app.domain.user.mail_quota import (
    MailQuota,
    normalize_email,
    site_mail_limit_reached,
)

logger = logging.getLogger(__name__)

VERIFICATION_CODE_TTL = 10 * 60
# Wrong guesses a code survives. A code is six digits, so without a cap its
# ten-minute life is enough to enumerate it.
MAX_VERIFICATION_ATTEMPTS = 5


class EmailCodePurpose(StrEnum):
    """What a mailed code is for. Each purpose keeps its codes and its mail
    quota apart, so a code sent to finish a sign-up cannot sign anyone in,
    and the other way round."""

    SIGN_UP = "sign_up"
    SIGN_IN = "sign_in"
    SUDO = "sudo"


@dataclass(frozen=True)
class _Mail:
    key_prefix: str
    quota: str
    subject: str
    heading: str
    intro: str
    text: str


_MAIL: dict[EmailCodePurpose, _Mail] = {
    EmailCodePurpose.SIGN_UP: _Mail(
        key_prefix="cheese:email_verification_code:",
        quota="email_verification",
        subject="[Cheese] Email Verification Code",
        heading="Email Verification",
        intro="Your verification code is:",
        text="Your Cheese verification code is: {code}",
    ),
    EmailCodePurpose.SIGN_IN: _Mail(
        key_prefix="cheese:email_sign_in_code:",
        quota="email_sign_in",
        subject="[Cheese] Sign-in Code",
        heading="Sign In",
        intro="Your sign-in code is:",
        text="Your Cheese sign-in code is: {code}",
    ),
    EmailCodePurpose.SUDO: _Mail(
        key_prefix="cheese:email_sudo_code:",
        quota="email_sudo",
        subject="[Cheese] Identity Confirmation Code",
        heading="Confirm It's You",
        intro="Your confirmation code is:",
        text="Your Cheese confirmation code is: {code}",
    ),
}

# The code and its failure count live in one hash, so they are issued, expire
# and are deleted together. Checking and counting in one script is what keeps
# a burst of simultaneous guesses from all being compared before any of them
# is counted.
_VERIFY_SCRIPT = """
local code = redis.call('HGET', KEYS[1], 'code')
if not code then
  return 0
end
if code == ARGV[1] then
  redis.call('DEL', KEYS[1])
  return 1
end
if redis.call('HINCRBY', KEYS[1], 'failures', 1) >= tonumber(ARGV[2]) then
  redis.call('DEL', KEYS[1])
end
return 0
"""


def generate_verification_code(length: int = 6) -> str:
    return "".join(secrets.choice(string.digits) for _ in range(length))


class EmailVerificationService:
    def __init__(
        self, redis: Redis, purpose: EmailCodePurpose = EmailCodePurpose.SIGN_UP
    ) -> None:
        self._redis = redis
        self._mail = _MAIL[purpose]
        self._quota = MailQuota(redis, self._mail.quota)
        self._sender = get_email_sender()

    def _key(self, email: str) -> str:
        return f"{self._mail.key_prefix}{normalize_email(email)}"

    async def claim(self, email: str, client: str | None = None) -> None:
        """Spend one mail of the quota for ``email``, or refuse the request.

        ``client``: the requester's address, for ``MailQuota.claim``.
        """
        claim = await self._quota.claim(email, client)
        if claim.site_full:
            raise site_mail_limit_reached()
        if not claim.granted:
            raise BadRequestError(
                "Please wait before requesting a new code",
                {
                    "reason": "email_code_too_soon",
                    "retryAfterSeconds": claim.retry_after_seconds,
                },
            )

    async def issue(self, email: str) -> bool:
        """Replace any earlier code for ``email`` with a new one and mail it.

        Returns False when a configured sender failed, leaving no code behind:
        nobody received it. The quota is the caller's to have claimed.
        """
        code = generate_verification_code()
        key = self._key(email)

        pipe = self._redis.pipeline(transaction=True)
        pipe.delete(key)
        pipe.hset(key, "code", code)
        pipe.expire(key, VERIFICATION_CODE_TTL)
        await pipe.execute()

        mail = self._mail
        body_html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #333;">{mail.heading}</h2>
            <p>{mail.intro}</p>
            <div style="background-color: #f5f5f5; padding: 20px;
                        text-align: center; margin: 20px 0;">
                <span style="font-size: 32px; font-weight: bold;
                             letter-spacing: 5px; color: #007bff;">{code}</span>
            </div>
            <p>This code will expire in 10 minutes.</p>
            <p style="color: #666; font-size: 12px;">
              If you didn't request this code, please ignore this email.
            </p>
        </div>
        """
        body_text = (
            mail.text.format(code=code) + "\nThis code will expire in 10 minutes."
        )

        if not self._sender.is_configured:
            # A deployment without mail (local development) keeps the code in
            # Redis and carries on, so the flows stay usable there.
            logger.warning(
                "Email not configured; %s code for %s was not sent", mail.quota, email
            )
            return True

        sent = await self._sender.send(
            to=email,
            subject=mail.subject,
            body_html=body_html,
            body_text=body_text,
        )
        if not sent:
            await self._redis.delete(key)
            return False

        logger.info("%s code sent to %s", mail.quota, email)
        return True

    async def send_verification_code(
        self, email: str, client: str | None = None
    ) -> None:
        """Claim the quota and mail a code, all within the request.

        ``client``: the requester's address, for ``MailQuota.claim``.
        """
        await self.claim(email, client)
        if not await self.issue(email):
            # Nobody received this code, so it must not count against the
            # resend quota either.
            await self._quota.give_back(email, client)
            raise SystemBusyError(
                "Failed to send the verification email. Please try again"
            )

    async def verify_code(self, email: str, code: str) -> bool:
        """Consume the code on a match. Each miss counts against the code, and
        the ``MAX_VERIFICATION_ATTEMPTS``-th one deletes it, so a new code
        has to be requested."""
        matched = await self._redis.eval(  # type: ignore[misc]
            _VERIFY_SCRIPT, 1, self._key(email), code, MAX_VERIFICATION_ATTEMPTS
        )
        return matched == 1
