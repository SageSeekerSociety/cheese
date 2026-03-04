from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pyotp
from redis.asyncio import Redis

from app.core.errors import ForbiddenError

if TYPE_CHECKING:
    pass


logger = logging.getLogger(__name__)

LOGIN_ATTEMPTS_PREFIX = "cheese:login_attempts:"
LOGIN_LOCKOUT_PREFIX = "cheese:login_lockout:"
TOTP_SECRET_PREFIX = "cheese:totp_secret:"
TOTP_PENDING_PREFIX = "cheese:totp_pending:"
SESSION_PREFIX = "cheese:session:"
USER_SESSIONS_PREFIX = "cheese:user_sessions:"
PASSWORD_RESET_PREFIX = "cheese:password_reset:"

MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_DURATION_SECONDS = 15 * 60
TOTP_PENDING_TTL = 10 * 60
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
            logger.warning("User %s locked out after %d failed attempts", username, attempts)

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

    async def start_2fa_setup(self, user_id: int, email: str) -> dict:
        secret = self.generate_secret()
        uri = self.get_provisioning_uri(secret, email)

        key = f"{TOTP_PENDING_PREFIX}{user_id}"
        await self._redis.setex(key, TOTP_PENDING_TTL, secret)

        return {
            "secret": secret,
            "provisioningUri": uri,
        }

    async def confirm_2fa_setup(self, user_id: int, code: str) -> str | None:
        key = f"{TOTP_PENDING_PREFIX}{user_id}"
        secret = await self._redis.get(key)
        if not secret:
            return None

        secret_str = secret.decode() if isinstance(secret, bytes) else secret
        if not self.verify_code(secret_str, code):
            return None

        await self._redis.delete(key)
        secret_key = f"{TOTP_SECRET_PREFIX}{user_id}"
        await self._redis.set(secret_key, secret_str)

        return secret_str

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

    async def disable_2fa(self, user_id: int) -> bool:
        key = f"{TOTP_SECRET_PREFIX}{user_id}"
        deleted = await self._redis.delete(key)
        return deleted > 0


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
        now = datetime.now(timezone.utc).isoformat()

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
        await self._redis.hset(session_key, mapping=session_data)
        await self._redis.expire(session_key, SESSION_TTL)

        user_sessions_key = f"{USER_SESSIONS_PREFIX}{user_id}"
        await self._redis.sadd(user_sessions_key, session_id)
        await self._redis.expire(user_sessions_key, SESSION_TTL)

        logger.info("Created session %s for user %d", session_id, user_id)
        return session_id

    async def get_session(self, session_id: str) -> dict | None:
        key = f"{SESSION_PREFIX}{session_id}"
        data = await self._redis.hgetall(key)
        if not data:
            return None

        return {k.decode(): v.decode() for k, v in data.items()}

    async def update_last_active(self, session_id: str) -> None:
        key = f"{SESSION_PREFIX}{session_id}"
        now = datetime.now(timezone.utc).isoformat()
        await self._redis.hset(key, "last_active_at", now)
        await self._redis.expire(key, SESSION_TTL)

    async def list_user_sessions(self, user_id: int) -> list[dict]:
        user_sessions_key = f"{USER_SESSIONS_PREFIX}{user_id}"
        session_ids = await self._redis.smembers(user_sessions_key)

        sessions = []
        for sid in session_ids:
            sid_str = sid.decode() if isinstance(sid, bytes) else sid
            session = await self.get_session(sid_str)
            if session:
                sessions.append(session)
            else:
                await self._redis.srem(user_sessions_key, sid)

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
        await self._redis.srem(user_sessions_key, session_id)

        logger.info("Revoked session %s for user %d", session_id, user_id)
        return True

    async def revoke_all_sessions(self, user_id: int, except_session_id: str | None = None) -> int:
        user_sessions_key = f"{USER_SESSIONS_PREFIX}{user_id}"
        session_ids = await self._redis.smembers(user_sessions_key)

        count = 0
        for sid in session_ids:
            sid_str = sid.decode() if isinstance(sid, bytes) else sid
            if except_session_id and sid_str == except_session_id:
                continue

            session_key = f"{SESSION_PREFIX}{sid_str}"
            await self._redis.delete(session_key)
            await self._redis.srem(user_sessions_key, sid)
            count += 1

        logger.info("Revoked %d sessions for user %d", count, user_id)
        return count


class PasswordResetService:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    def generate_reset_token(self) -> str:
        return secrets.token_urlsafe(32)

    async def create_reset_token(self, user_id: int, email: str) -> str:
        token = self.generate_reset_token()
        key = f"{PASSWORD_RESET_PREFIX}{token}"

        data = {
            "user_id": str(user_id),
            "email": email,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        await self._redis.hset(key, mapping=data)
        await self._redis.expire(key, PASSWORD_RESET_TTL)

        logger.info("Created password reset token for user %d", user_id)
        return token

    async def validate_reset_token(self, token: str) -> dict | None:
        key = f"{PASSWORD_RESET_PREFIX}{token}"
        data = await self._redis.hgetall(key)

        if not data:
            return None

        return {k.decode(): v.decode() for k, v in data.items()}

    async def consume_reset_token(self, token: str) -> dict | None:
        data = await self.validate_reset_token(token)
        if data:
            key = f"{PASSWORD_RESET_PREFIX}{token}"
            await self._redis.delete(key)
        return data
