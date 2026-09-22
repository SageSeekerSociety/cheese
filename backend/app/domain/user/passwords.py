"""Legacy bcrypt passwords.

bcrypt only reads the first 72 bytes of its input, and bcrypt 5 raises
``ValueError`` rather than truncating. Every caller goes through here so that
limit is handled once: a password that long can never match a stored hash, and
one that long must be refused before anything is written.
"""

import asyncio

import bcrypt

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
