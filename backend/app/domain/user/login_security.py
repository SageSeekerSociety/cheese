import hashlib
import hmac
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pyotp
from redis.asyncio import Redis
from sqlalchemy import and_, delete, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import Purpose, decrypt, encrypt, keyed_digest, keyed_digests
from app.core.errors import ForbiddenError
from app.domain.user.models import UserBackupCode, UserTwoFactor

logger = logging.getLogger(__name__)

LOGIN_ATTEMPTS_PREFIX = "cheese:login_attempts:"
LOGIN_LOCKOUT_PREFIX = "cheese:login_lockout:"
# The SECOND login step gets its own budget, keyed by user id rather than
# username (#357): by the time a 2fa_pending token is presented there is no
# username in the request, and — more importantly — a *successful* password
# step clears the username counter, so sharing it would hand an attacker who
# already holds the password an unlimited supply of 2FA guesses.
TWO_FACTOR_ATTEMPTS_PREFIX = "cheese:2fa_attempts:"
TWO_FACTOR_LOCKOUT_PREFIX = "cheese:2fa_lockout:"
# Backup codes are one-shot high-entropy credentials; counting them together
# with TOTP would let ordinary TOTP typos spend their budget and vice versa.
BACKUP_CODE_ATTEMPTS_PREFIX = "cheese:2fa_backup_attempts:"
BACKUP_CODE_LOCKOUT_PREFIX = "cheese:2fa_backup_lockout:"
# Re-proving 2FA inside a live session (#389) is the same secret under a
# different threat model, so it gets its own keys: sharing the login budget
# would let whoever stole a session lock the owner out of signing in — turning
# a rate limit into a denial of service — and would let the owner's own typos
# at the login screen shrink the step-up allowance.
STEP_UP_2FA_ATTEMPTS_PREFIX = "cheese:2fa_stepup_attempts:"
STEP_UP_2FA_LOCKOUT_PREFIX = "cheese:2fa_stepup_lockout:"
# Re-proving the *password* inside a live session (``/auth/sudo`` method
# password) is a third budget again. It cannot share the login one,
# which is keyed by username and cleared by every successful sign-in, and it
# must not share the step-up 2FA one: a password typo would then spend the
# allowance for the other factor, and each factor is supposed to survive the
# other being ground down.
STEP_UP_PASSWORD_ATTEMPTS_PREFIX = "cheese:sudo_password_attempts:"
STEP_UP_PASSWORD_LOCKOUT_PREFIX = "cheese:sudo_password_lockout:"
# How long the secret offered by the first step of 2FA setup waits for its
# confirming code. Only an offered secret can be confirmed, so the
# re-authentication the first step asks for covers the whole setup.
TOTP_PENDING_TTL_S = 600
SESSION_PREFIX = "cheese:session:"
USER_SESSIONS_PREFIX = "cheese:user_sessions:"
PASSWORD_RESET_PREFIX = "cheese:password_reset:"

MAX_LOGIN_ATTEMPTS = 5
MAX_TWO_FACTOR_ATTEMPTS = 5
# Tighter than the shared 2FA budget: a backup code is read off a saved list,
# not typed from a phone under time pressure, so three misses is already
# generous — and it is the credential worth guarding hardest, being the one
# that needs no device.
MAX_BACKUP_CODE_ATTEMPTS = 3
# Its own knob rather than an alias of the login figure: the two budgets bound
# different attacks and either can be tightened without dragging the other
# along. Same number today because the credential and its entropy are the same.
MAX_STEP_UP_2FA_ATTEMPTS = 5
# Same figure as the login budget, for the same credential — but its own knob,
# because the two bound different attacks.
MAX_STEP_UP_PASSWORD_ATTEMPTS = 5
LOCKOUT_DURATION_SECONDS = 15 * 60
PASSWORD_RESET_TTL = 30 * 60
SESSION_TTL = 30 * 24 * 60 * 60


class LoginRateLimiter:
    """Failed-attempt budget for one credential step, keyed by ``subject``.

    ``subject`` is a username here and a user id in the 2FA subclasses below —
    which key a step uses is part of what makes it a *separate* budget, so it
    is deliberately the caller's choice rather than something inferred.
    """

    _attempts_prefix = LOGIN_ATTEMPTS_PREFIX
    _lockout_prefix = LOGIN_LOCKOUT_PREFIX
    _max_attempts = MAX_LOGIN_ATTEMPTS
    _what = "login"

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def is_locked_out(self, subject: str) -> bool:
        key = f"{self._lockout_prefix}{subject}"
        return await self._redis.exists(key) > 0

    async def get_remaining_lockout_seconds(self, subject: str) -> int:
        key = f"{self._lockout_prefix}{subject}"
        ttl = await self._redis.ttl(key)
        return max(0, ttl)

    async def consume_attempt(self, subject: str) -> int | None:
        """Take one attempt from the budget *before* the credential is checked.

        Returns how many attempts remain after this one, or ``None`` if the
        budget was already spent and this request must be refused.

        Checking a counter and only writing it after a failure is a
        check-then-act race: a thousand requests fired at once all read the
        counter before any of them writes, so all thousand get through a
        5-attempt budget. Spending the slot up front closes that, because
        ``INCR`` is atomic — each request in the burst gets a distinct number
        and only the first few are under the cap. Callers clear the counter on
        success, so a legitimate login leaves nothing behind.
        """
        attempts_key = f"{self._attempts_prefix}{subject}"
        attempts = await self._redis.incr(attempts_key)
        await self._redis.expire(attempts_key, LOCKOUT_DURATION_SECONDS)

        if attempts >= self._max_attempts:
            lockout_key = f"{self._lockout_prefix}{subject}"
            await self._redis.setex(lockout_key, LOCKOUT_DURATION_SECONDS, "1")
            logger.warning(
                "%s locked out for %s after %d attempts",
                subject,
                self._what,
                attempts,
            )

        if attempts > self._max_attempts:
            return None
        return self._max_attempts - attempts

    async def clear_attempts(self, subject: str) -> None:
        attempts_key = f"{self._attempts_prefix}{subject}"
        lockout_key = f"{self._lockout_prefix}{subject}"
        await self._redis.delete(attempts_key, lockout_key)

    async def get_attempt_count(self, subject: str) -> int:
        key = f"{self._attempts_prefix}{subject}"
        val = await self._redis.get(key)
        return int(val) if val else 0


class TwoFactorRateLimiter(LoginRateLimiter):
    """Budget for the second login step, keyed by ``str(user_id)`` (#357).

    Before this existed the step had no counter at all: password-correct +
    wrong TOTP moved nothing, so whoever held a leaked password could grind
    3-in-10^6 (``valid_window=1``) until it hit — against the one control
    whose entire job is to survive a leaked password.
    """

    _attempts_prefix = TWO_FACTOR_ATTEMPTS_PREFIX
    _lockout_prefix = TWO_FACTOR_LOCKOUT_PREFIX
    _max_attempts = MAX_TWO_FACTOR_ATTEMPTS
    _what = "2fa"


class BackupCodeRateLimiter(LoginRateLimiter):
    """Budget for backup-code guesses only, keyed by ``str(user_id)``.

    Stacked *under* TwoFactorRateLimiter, not instead of it: a backup-code
    attempt spends both budgets, a TOTP attempt spends only the 2FA one.
    """

    _attempts_prefix = BACKUP_CODE_ATTEMPTS_PREFIX
    _lockout_prefix = BACKUP_CODE_LOCKOUT_PREFIX
    _max_attempts = MAX_BACKUP_CODE_ATTEMPTS
    _what = "2fa backup code"


class StepUpTwoFactorRateLimiter(LoginRateLimiter):
    """Budget for re-proving 2FA inside an existing session (#389).

    Covers ``/auth/sudo`` (method=totp), which checks the same TOTP secret as
    login but against an attacker who already holds a live session rather than
    a leaked password. That is the whole surface: ``/{userId}/2fa/disable``
    takes no code of its own, only the ticket sudo hands out, so there is one
    place to guess and it is this one.

    Separate from the *login* budget: see the prefix comment.
    """

    _attempts_prefix = STEP_UP_2FA_ATTEMPTS_PREFIX
    _lockout_prefix = STEP_UP_2FA_LOCKOUT_PREFIX
    _max_attempts = MAX_STEP_UP_2FA_ATTEMPTS
    _what = "2fa step-up"


class StepUpPasswordRateLimiter(LoginRateLimiter):
    """Budget for re-proving the password inside an existing session (#389)."""

    _attempts_prefix = STEP_UP_PASSWORD_ATTEMPTS_PREFIX
    _lockout_prefix = STEP_UP_PASSWORD_LOCKOUT_PREFIX
    _max_attempts = MAX_STEP_UP_PASSWORD_ATTEMPTS
    _what = "password step-up"


class TOTPService:
    """The TOTP second factor, kept in Postgres (#1482).

    It used to live only in Redis, where losing the data silently switched
    every user's second factor off. Attempt budgets stay in Redis: losing
    those only resets a counter.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def generate_secret(self) -> str:
        return pyotp.random_base32()

    def get_provisioning_uri(self, secret: str, email: str) -> str:
        totp = pyotp.TOTP(secret)
        return totp.provisioning_uri(name=email, issuer_name="Cheese")

    def verify_code(self, secret: str, code: str) -> bool:
        totp = pyotp.TOTP(secret)
        return totp.verify(code, valid_window=1)

    async def _factor(self, user_id: int) -> UserTwoFactor | None:
        return await self._session.get(UserTwoFactor, user_id)

    async def _factor_for_update(self, user_id: int) -> UserTwoFactor:
        factor = await self._factor(user_id)
        if factor is None:
            factor = UserTwoFactor(user_id=user_id)
            self._session.add(factor)
        return factor

    async def is_2fa_enabled(self, user_id: int) -> bool:
        factor = await self._factor(user_id)
        return factor is not None and factor.secret is not None

    async def verify_2fa(self, user_id: int, code: str) -> bool:
        factor = await self._factor(user_id)
        if factor is None or factor.secret is None:
            return False
        secret = decrypt(Purpose.TOTP_SECRET, factor.secret, bound_to=f"user:{user_id}")
        return self.verify_code(secret, code)

    async def offer_secret(self, user_id: int) -> str:
        """Generate a secret for this user to confirm, replacing any earlier
        offer."""
        secret = self.generate_secret()
        factor = await self._factor_for_update(user_id)
        factor.pending_secret = encrypt(
            Purpose.TOTP_PENDING_SECRET, secret, bound_to=f"user:{user_id}"
        )
        factor.pending_expires_at = datetime.now(UTC) + timedelta(
            seconds=TOTP_PENDING_TTL_S
        )
        await self._session.flush()
        return secret

    async def enable_2fa(self, user_id: int, secret: str, code: str) -> bool:
        """Confirm setup and persist the secret. Returns False unless ``secret``
        is the one last offered to this user and ``code`` is live for it.

        The client echoes the secret back (reference contract), but only an
        offered one is accepted: otherwise the confirming step would take any
        secret at all, and the step that offered it — the one gated on
        re-authentication — could simply be skipped.
        """
        factor = await self._factor(user_id)
        if (
            factor is None
            or factor.pending_secret is None
            or factor.pending_expires_at is None
            or factor.pending_expires_at <= datetime.now(UTC)
        ):
            return False
        offered = decrypt(
            Purpose.TOTP_PENDING_SECRET,
            factor.pending_secret,
            bound_to=f"user:{user_id}",
        )
        if not hmac.compare_digest(offered, secret) or not self.verify_code(
            secret, code
        ):
            return False
        factor.secret = encrypt(Purpose.TOTP_SECRET, secret, bound_to=f"user:{user_id}")
        factor.pending_secret = None
        factor.pending_expires_at = None
        await self._session.flush()
        return True

    async def disable_2fa(self, user_id: int) -> bool:
        await self._session.execute(
            delete(UserBackupCode).where(UserBackupCode.user_id == user_id)
        )
        removed = await self._session.execute(
            delete(UserTwoFactor)
            .where(UserTwoFactor.user_id == user_id)
            .returning(UserTwoFactor.secret)
        )
        return any(secret is not None for secret in removed.scalars())

    # --- Backup codes (reference parity): 10 one-time codes, 8 hex chars. ---
    #
    # Stored as HMAC-SHA256 under a subkey of DATA_ENCRYPTION_KEY, not as a
    # plain or slow hash. A code has only 32 bits of entropy, so any unkeyed
    # hash — bcrypt included — falls to exhausting that space from a database
    # dump; the key lives outside the database, and without it a dump is
    # useless. A keyed digest is also exact, so a code is consumed by one
    # indexed DELETE, which is what makes it single-use under concurrency. The
    # message is the user plus the code's SHA-256, which binds each digest to
    # its owner and let the codes that were kept in Redis as SHA-256 digests
    # carry over without the codes themselves.

    @staticmethod
    def _code_message(user_id: int, code: str) -> bytes:
        normalized = code.strip().lower().encode()
        return f"user:{user_id}:".encode() + hashlib.sha256(normalized).digest()

    async def generate_backup_codes(self, user_id: int) -> list[str]:
        codes = [secrets.token_hex(4) for _ in range(10)]
        await self._session.execute(
            delete(UserBackupCode).where(UserBackupCode.user_id == user_id)
        )
        for code in codes:
            key_id, digest = keyed_digest(
                Purpose.TOTP_BACKUP_CODE, self._code_message(user_id, code)
            )
            self._session.add(
                UserBackupCode(user_id=user_id, key_id=key_id, digest=digest)
            )
        await self._session.flush()
        return codes

    async def verify_backup_code(self, user_id: int, code: str) -> bool:
        """One-time: a matching code is deleted, and the deletion committed,
        before this returns — a later failure in the request cannot give it
        back, and of two concurrent uses only one finds the row."""
        digests = keyed_digests(
            Purpose.TOTP_BACKUP_CODE, self._code_message(user_id, code)
        )
        used = await self._session.execute(
            delete(UserBackupCode)
            .where(
                UserBackupCode.user_id == user_id,
                or_(
                    *(
                        and_(
                            UserBackupCode.key_id == key_id,
                            UserBackupCode.digest == digest,
                        )
                        for key_id, digest in digests.items()
                    )
                ),
            )
            .returning(UserBackupCode.id)
        )
        if used.first() is None:
            return False
        await self._session.commit()
        return True

    # --- always_required flag (surfaced in /2fa/status and /2fa/settings).
    # With no trusted-device feature, login asks for 2FA whenever it is
    # enabled, so the flag currently only affects what the UI reports. ---

    async def is_always_required(self, user_id: int) -> bool:
        factor = await self._factor(user_id)
        return factor is not None and factor.always_required

    async def set_always_required(self, user_id: int, value: bool) -> None:
        factor = await self._factor_for_update(user_id)
        factor.always_required = value
        await self._session.flush()


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

    async def consume_reset_token(self, token: str) -> dict | None:
        """Read and delete in one MULTI/EXEC, so of two requests presenting the
        same token only the first finds anything."""
        key = f"{PASSWORD_RESET_PREFIX}{token}"
        pipe = self._redis.pipeline(transaction=True)
        pipe.hgetall(key)
        pipe.delete(key)
        data, _deleted = await pipe.execute()

        if not data:
            return None

        # redis client is decode_responses=False → values are bytes at runtime,
        # but the redis-py stubs don't model that and type them as str.
        return {k.decode(): v.decode() for k, v in data.items()}
