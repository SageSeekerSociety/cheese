import logging
import secrets
import string

from redis.asyncio import Redis

from app.core.email import get_email_sender
from app.core.errors import BadRequestError, SystemBusyError

logger = logging.getLogger(__name__)

VERIFICATION_CODE_PREFIX = "cheese:email_verification_code:"
VERIFICATION_CODE_TTL = 10 * 60
# Wrong guesses a code survives. A code is six digits, so without a cap its
# ten-minute life is enough to enumerate it.
MAX_VERIFICATION_ATTEMPTS = 5

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
    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._sender = get_email_sender()

    async def send_verification_code(self, email: str) -> None:
        code = generate_verification_code()
        key = f"{VERIFICATION_CODE_PREFIX}{email}"

        if await self._redis.ttl(key) > VERIFICATION_CODE_TTL - 60:
            raise BadRequestError("Please wait before requesting a new code")

        pipe = self._redis.pipeline(transaction=True)
        pipe.delete(key)
        pipe.hset(key, "code", code)
        pipe.expire(key, VERIFICATION_CODE_TTL)
        await pipe.execute()

        subject = "[Cheese] Email Verification Code"
        body_html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #333;">Email Verification</h2>
            <p>Your verification code is:</p>
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
        body_text = f"Your Cheese verification code is: {code}\nThis code will expire in 10 minutes."  # noqa: E501

        if not self._sender.is_configured:
            # A deployment without mail (local development) keeps the code in
            # Redis and carries on, so registration stays usable there.
            logger.warning(
                "Email not configured; verification code for %s was not sent", email
            )
            return

        sent = await self._sender.send(
            to=email,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
        )
        if not sent:
            # Nobody received this code, so it must not hold the resend
            # cooldown either.
            await self._redis.delete(key)
            raise SystemBusyError(
                "Failed to send the verification email. Please try again"
            )

        logger.info("Verification code sent to %s", email)

    async def verify_code(self, email: str, code: str) -> bool:
        """Consume the code on a match. Each miss counts against the code, and
        the ``MAX_VERIFICATION_ATTEMPTS``-th one deletes it, so a new code
        has to be requested."""
        key = f"{VERIFICATION_CODE_PREFIX}{email}"
        matched = await self._redis.eval(
            _VERIFY_SCRIPT, 1, key, code, MAX_VERIFICATION_ATTEMPTS
        )
        return matched == 1
