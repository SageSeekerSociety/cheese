import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import jwt
import pyotp
from redis.asyncio import Redis

from app.core.config import settings
from app.core.errors import ForbiddenError

if TYPE_CHECKING:
    pass


logger = logging.getLogger(__name__)

LOGIN_ATTEMPTS_PREFIX = "cheese:login_attempts:"
LOGIN_LOCKOUT_PREFIX = "cheese:login_lockout:"
TOTP_SECRET_PREFIX = "cheese:totp_secret:"
TOTP_BACKUP_PREFIX = "cheese:totp_backup:"
TOTP_ALWAYS_PREFIX = "cheese:totp_always:"
SESSION_PREFIX = "cheese:session:"
USER_SESSIONS_PREFIX = "cheese:user_sessions:"
PASSWORD_RESET_PREFIX = "cheese:password_reset:"

MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_DURATION_SECONDS = 15 * 60
PASSWORD_RESET_TTL = 30 * 60
SESSION_TTL = 30 * 24 * 60 * 60


class LoginRateLimiter:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def is_locked_out(self, username: str) -> bool:
        key = f"{LOGIN_LOCKOUT_PREFIX}{username}"
        return await self._redis.exists(key) > 0

    async def get_remaining_lockout_seconds(self, username: str) -> int:
        key = f"{LOGIN_LOCKOUT_PREFIX}{username}"
        ttl = await self._redis.ttl(key)
        return max(0, ttl)

    async def record_failed_attempt(self, username: str) -> int:
        attempts_key = f"{LOGIN_ATTEMPTS_PREFIX}{username}"
        attempts = await self._redis.incr(attempts_key)
        await self._redis.expire(attempts_key, LOCKOUT_DURATION_SECONDS)

        if attempts >= MAX_LOGIN_ATTEMPTS:
            lockout_key = f"{LOGIN_LOCKOUT_PREFIX}{username}"
            await self._redis.setex(lockout_key, LOCKOUT_DURATION_SECONDS, "1")
            logger.warning(
                "User %s locked out after %d failed attempts", username, attempts
            )

        return attempts

    async def clear_attempts(self, username: str) -> None:
        attempts_key = f"{LOGIN_ATTEMPTS_PREFIX}{username}"
        lockout_key = f"{LOGIN_LOCKOUT_PREFIX}{username}"
        await self._redis.delete(attempts_key, lockout_key)

    async def get_attempt_count(self, username: str) -> int:
        key = f"{LOGIN_ATTEMPTS_PREFIX}{username}"
        val = await self._redis.get(key)
        return int(val) if val else 0


class TOTPService:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    def generate_secret(self) -> str:
        return pyotp.random_base32()

    def get_provisioning_uri(self, secret: str, email: str) -> str:
        totp = pyotp.TOTP(secret)
        return totp.provisioning_uri(name=email, issuer_name="Cheese")

    def verify_code(self, secret: str, code: str) -> bool:
        totp = pyotp.TOTP(secret)
        return totp.verify(code, valid_window=1)

    async def is_2fa_enabled(self, user_id: int) -> bool:
        key = f"{TOTP_SECRET_PREFIX}{user_id}"
        return await self._redis.exists(key) > 0

    async def verify_2fa(self, user_id: int, code: str) -> bool:
        key = f"{TOTP_SECRET_PREFIX}{user_id}"
        secret = await self._redis.get(key)
        if not secret:
            return False

        secret_str = secret.decode() if isinstance(secret, bytes) else secret
        return self.verify_code(secret_str, code)

    async def enable_2fa(self, user_id: int, secret: str, code: str) -> bool:
        """Confirm setup against a client-round-tripped secret (reference
        contract: init hands the secret to the client, confirm sends it back
        with a live code) and persist it. Returns False on a bad code."""
        if not self.verify_code(secret, code):
            return False
        await self._redis.set(f"{TOTP_SECRET_PREFIX}{user_id}", secret)
        return True

    async def disable_2fa(self, user_id: int) -> bool:
        deleted = await self._redis.delete(
            f"{TOTP_SECRET_PREFIX}{user_id}",
            f"{TOTP_BACKUP_PREFIX}{user_id}",
            f"{TOTP_ALWAYS_PREFIX}{user_id}",
        )
        return deleted > 0

    # --- Backup codes (reference parity): 10 one-time codes, 8 hex chars,
    # stored as SHA-256 digests so a Redis dump does not leak usable codes. ---

    async def generate_backup_codes(self, user_id: int) -> list[str]:
        import hashlib
        import secrets as _secrets

        codes = [_secrets.token_hex(4) for _ in range(10)]
        key = f"{TOTP_BACKUP_PREFIX}{user_id}"
        pipe = self._redis.pipeline()
        pipe.delete(key)
        pipe.sadd(key, *[hashlib.sha256(c.encode()).hexdigest() for c in codes])
        await pipe.execute()
        return codes

    async def verify_backup_code(self, user_id: int, code: str) -> bool:
        """One-time: a matching code is atomically removed on use."""
        import hashlib

        digest = hashlib.sha256(code.strip().lower().encode()).hexdigest()
        removed = await self._redis.srem(f"{TOTP_BACKUP_PREFIX}{user_id}", digest)
        return removed > 0

    # --- always_required flag (surfaced in /2fa/status and /2fa/settings).
    # With no trusted-device feature, login asks for 2FA whenever it is
    # enabled, so the flag currently only affects what the UI reports. ---

    async def is_always_required(self, user_id: int) -> bool:
        return await self._redis.exists(f"{TOTP_ALWAYS_PREFIX}{user_id}") > 0

    async def set_always_required(self, user_id: int, value: bool) -> None:
        key = f"{TOTP_ALWAYS_PREFIX}{user_id}"
        if value:
            await self._redis.set(key, b"1")
        else:
            await self._redis.delete(key)


class SessionManager:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def create_session(
        self,
        user_id: int,
        device_info: str = "",
        ip_address: str = "",
        user_agent: str = "",
    ) -> str:
        session_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()

        session_data = {
            "session_id": session_id,
            "user_id": str(user_id),
            "device_info": device_info,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "created_at": now,
            "last_active_at": now,
        }

        session_key = f"{SESSION_PREFIX}{session_id}"
        await self._redis.hset(session_key, mapping=session_data)  # type: ignore[misc]
        await self._redis.expire(session_key, SESSION_TTL)

        user_sessions_key = f"{USER_SESSIONS_PREFIX}{user_id}"
        await self._redis.sadd(user_sessions_key, session_id)  # type: ignore[misc]
        await self._redis.expire(user_sessions_key, SESSION_TTL)

        logger.info("Created session %s for user %d", session_id, user_id)
        return session_id

    async def get_session(self, session_id: str) -> dict | None:
        key = f"{SESSION_PREFIX}{session_id}"
        data = await self._redis.hgetall(key)  # type: ignore[misc]
        if not data:
            return None

        # redis client is decode_responses=False → values are bytes at runtime,
        # but the redis-py stubs don't model that and type them as str.
        return {k.decode(): v.decode() for k, v in data.items()}  # type: ignore[attr-defined]

    async def update_last_active(self, session_id: str) -> None:
        key = f"{SESSION_PREFIX}{session_id}"
        now = datetime.now(UTC).isoformat()
        await self._redis.hset(key, "last_active_at", now)  # type: ignore[misc]
        await self._redis.expire(key, SESSION_TTL)

    async def list_user_sessions(self, user_id: int) -> list[dict]:
        user_sessions_key = f"{USER_SESSIONS_PREFIX}{user_id}"
        session_ids = await self._redis.smembers(user_sessions_key)  # type: ignore[misc]

        sessions = []
        for sid in session_ids:
            sid_str = sid.decode() if isinstance(sid, bytes) else sid
            session = await self.get_session(sid_str)
            if session:
                sessions.append(session)
            else:
                await self._redis.srem(user_sessions_key, sid)  # type: ignore[misc]

        sessions.sort(key=lambda s: s.get("last_active_at", ""), reverse=True)
        return sessions

    async def revoke_session(self, session_id: str, user_id: int) -> bool:
        session = await self.get_session(session_id)
        if not session:
            return False

        if int(session.get("user_id", 0)) != user_id:
            raise ForbiddenError("Cannot revoke session of another user")

        session_key = f"{SESSION_PREFIX}{session_id}"
        await self._redis.delete(session_key)

        user_sessions_key = f"{USER_SESSIONS_PREFIX}{user_id}"
        await self._redis.srem(user_sessions_key, session_id)  # type: ignore[misc]

        logger.info("Revoked session %s for user %d", session_id, user_id)
        return True

    async def revoke_all_sessions(
        self, user_id: int, except_session_id: str | None = None
    ) -> int:
        user_sessions_key = f"{USER_SESSIONS_PREFIX}{user_id}"
        session_ids = await self._redis.smembers(user_sessions_key)  # type: ignore[misc]

        count = 0
        for sid in session_ids:
            sid_str = sid.decode() if isinstance(sid, bytes) else sid
            if except_session_id and sid_str == except_session_id:
                continue

            session_key = f"{SESSION_PREFIX}{sid_str}"
            await self._redis.delete(session_key)
            await self._redis.srem(user_sessions_key, sid)  # type: ignore[misc]
            count += 1

        logger.info("Revoked %d sessions for user %d", count, user_id)
        return count


class PasswordResetService:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    def generate_reset_token(self, username: str) -> str:
        """Generate a JWT token that the frontend can decode to get username."""
        payload = {
            "payload": {
                "authorization": {
                    "username": username,
                }
            },
            "exp": datetime.now(UTC) + timedelta(seconds=PASSWORD_RESET_TTL),
        }
        return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")

    async def create_reset_token(self, user_id: int, email: str, username: str) -> str:
        token = self.generate_reset_token(username)
        key = f"{PASSWORD_RESET_PREFIX}{token}"

        data = {
            "user_id": str(user_id),
            "email": email,
            "username": username,
            "created_at": datetime.now(UTC).isoformat(),
        }

        await self._redis.hset(key, mapping=data)  # type: ignore[misc]
        await self._redis.expire(key, PASSWORD_RESET_TTL)

        logger.info("Created password reset token for user %d", user_id)
        return token

    async def validate_reset_token(self, token: str) -> dict | None:
        key = f"{PASSWORD_RESET_PREFIX}{token}"
        data = await self._redis.hgetall(key)  # type: ignore[misc]

        if not data:
            return None

        # redis client is decode_responses=False → values are bytes at runtime,
        # but the redis-py stubs don't model that and type them as str.
        return {k.decode(): v.decode() for k, v in data.items()}  # type: ignore[attr-defined]

    async def consume_reset_token(self, token: str) -> dict | None:
        data = await self.validate_reset_token(token)
        if data:
            key = f"{PASSWORD_RESET_PREFIX}{token}"
            await self._redis.delete(key)
        return data
