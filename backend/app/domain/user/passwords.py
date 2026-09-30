"""Legacy bcrypt passwords.

bcrypt only reads the first 72 bytes of its input, and bcrypt 5 raises
``ValueError`` rather than truncating. Every caller goes through here so that
limit is handled once: a password that long can never match a stored hash, and
one that long must be refused before anything is written.
"""

import asyncio
import re

import bcrypt

from app.core.errors import BadRequestError, UnprocessableEntityError

MAX_PASSWORD_BYTES = 72


def password_too_long(password: str) -> bool:
    return len(password.encode("utf-8")) > MAX_PASSWORD_BYTES


async def hash_password(password: str) -> str:
    """Raises ``ValueError`` for a password over ``MAX_PASSWORD_BYTES``."""
    hashed = await asyncio.to_thread(
        bcrypt.hashpw, password.encode("utf-8"), bcrypt.gensalt()
    )
    return hashed.decode("utf-8")


async def check_password(password: str, hashed: str) -> bool:
    if password_too_long(password):
        return False
    return await asyncio.to_thread(
        bcrypt.checkpw, password.encode("utf-8"), hashed.encode("utf-8")
    )


# At least 8 characters, a letter, a digit and an ASCII symbol: the web
# client's rule (REGEX_PASSWORD), so the form and the server agree on it.
_NEW_PASSWORD_PATTERN = re.compile(
    r"^(?=.*[a-zA-Z])(?=.*\d)(?=.*[\x00-\x2F\x3A-\x40\x5B-\x60\x7B-\x7F]).{8,}$"
)


def require_new_password(password: str) -> None:
    """The rule a password chosen for an account must meet, and the length
    bcrypt can hold. Checked before anything single-use is spent."""
    if not _NEW_PASSWORD_PATTERN.match(password):
        raise UnprocessableEntityError(
            "Use at least 8 characters, with a letter, a number, "
            "and a special character"
        )
    # Refused before anything is consumed: bcrypt cannot hash it, and failing
    # after the email code or reset token is spent would cost the user both.
    if password_too_long(password):
        raise BadRequestError(f"Password must not exceed {MAX_PASSWORD_BYTES} bytes")
