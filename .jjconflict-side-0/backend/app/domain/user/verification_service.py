import logging
import secrets
import string

from redis.asyncio import Redis

from app.core.email import get_email_sender
from app.core.errors import BadRequestError

logger = logging.getLogger(__name__)

VERIFICATION_CODE_PREFIX = "cheese:email_verification:"
VERIFICATION_CODE_TTL = 10 * 60


def generate_verification_code(length: int = 6) -> str:
    return "".join(secrets.choice(string.digits) for _ in range(length))


class EmailVerificationService:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._sender = get_email_sender()

    async def send_verification_code(self, email: str) -> bool:
        code = generate_verification_code()
        key = f"{VERIFICATION_CODE_PREFIX}{email}"

        existing = await self._redis.get(key)
        if existing:
            ttl = await self._redis.ttl(key)
            if ttl > VERIFICATION_CODE_TTL - 60:
                raise BadRequestError("Please wait before requesting a new code")

        await self._redis.setex(key, VERIFICATION_CODE_TTL, code)

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

        success = await self._sender.send(
            to=email,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
        )

        if success:
            logger.info("Verification code sent to %s", email)
        else:
            logger.warning(
                "Failed to send verification code to %s (email not configured)", email
            )

        return True

    async def verify_code(self, email: str, code: str) -> bool:
        key = f"{VERIFICATION_CODE_PREFIX}{email}"
        stored_code = await self._redis.get(key)

        if stored_code is None:
            return False

        # redis client is decode_responses=False → get() returns bytes at runtime,
        # but the redis-py stubs don't model that and type it as str.
        if stored_code.decode() != code:  # type: ignore[attr-defined]
            return False

        await self._redis.delete(key)
        return True

    async def check_code_exists(self, email: str) -> bool:
        key = f"{VERIFICATION_CODE_PREFIX}{email}"
        return await self._redis.exists(key) > 0
