import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
import pyotp
from redis.asyncio import Redis
from sqlalchemy import and_, delete, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import Purpose, decrypt, encrypt, keyed_digest, keyed_digests
from app.domain.user.models import UserBackupCode, UserTwoFactor

logger = logging.getLogger(__name__)

# The password step is slowed per username rather than locked: see LoginDelay.
LOGIN_FAILURES_PREFIX = "cheese:login_failures:"
LOGIN_WAIT_PREFIX = "cheese:login_wait:"
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
PASSWORD_RESET_PREFIX = "cheese:password_reset:"

# Wrong passwords a username takes before each further one makes the next
# attempt wait: FIRST_WAIT after the last free one, doubling per failure up to
# MAX_WAIT. The cap is what bounds a stranger's hold over somebody else's
# account: however many wrong passwords they send, the owner is never kept
# waiting longer than it. It also bounds guessing: once at the cap, one
# password per MAX_WAIT, about 290 a day.
LOGIN_FREE_FAILURES = 5
LOGIN_FIRST_WAIT_SECONDS = 30
LOGIN_MAX_WAIT_SECONDS = 5 * 60
# Failures are forgotten this long after the last one: long enough that
# pausing between bursts does not buy a guesser the free attempts again
# sooner than waiting at the cap would. A right password forgets them at once.
LOGIN_FAILURE_WINDOW_SECONDS = 60 * 60
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


# Failures one client address may make at each credential step, per window.
# Generous on purpose: a campus puts many of its users behind a few NAT
# addresses, so this only stops one source spraying guesses across accounts,
# and the per-account limits above do the fine work.
CLIENT_FAILURE_PREFIX = "cheese:client_failures:"
CLIENT_MAX_FAILURES = 100
CLIENT_FAILURE_WINDOW_SECONDS = 15 * 60

# Counted in one script for the reason consume_attempt gives. A refused attempt
# is handed straight back, so it neither counts nor extends the window.
_CLIENT_SPEND_SCRIPT = """
local failures = redis.call('INCR', KEYS[1])
if failures == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[2])
end
if failures > tonumber(ARGV[1]) then
  redis.call('DECR', KEYS[1])
  return math.max(redis.call('TTL', KEYS[1]), 1)
end
return 0
"""

_CLIENT_REFUND_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then
  redis.call('DECR', KEYS[1])
end
return 1
"""

# Checked and counted in one script, for the reason consume_attempt gives.
# Returns {1, wait this attempt starts if it fails} or {0, seconds still to wait}.
_LOGIN_ADMIT_SCRIPT = """
local waiting = redis.call('TTL', KEYS[2])
if waiting > 0 then
  return {0, waiting}
end
local failures = redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], ARGV[1])
local free = tonumber(ARGV[2])
if failures < free then
  return {1, 0}
end
local wait = math.min(
  tonumber(ARGV[3]) * 2 ^ (failures - free), tonumber(ARGV[4]))
wait = math.floor(wait)
redis.call('SET', KEYS[2], '1', 'EX', wait)
return {1, wait}
"""


@dataclass(frozen=True)
class LoginAdmission:
    admitted: bool
    #: Admitted: how long the next attempt must wait if this one fails.
    #: Refused: how long until an attempt is admitted.
    wait_seconds: int


class LoginDelay:
    """Slows the password step for one username as its failures mount.

    Not a lock, because anybody can send wrong passwords for a username they
    do not own. A lock would let them keep its owner out; a wait capped at
    ``LOGIN_MAX_WAIT_SECONDS`` only delays the owner, and never by more.

    An attempt is counted as a failure when admitted, before the password is
    checked, for the reason ``AttemptLimiter.consume_attempt`` gives; the
    caller clears the count once the password holds. Attempts refused while a
    wait runs are not counted, so sending more of them does not lengthen it.
    """

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    def _keys(self, username: str) -> tuple[str, str]:
        return f"{LOGIN_FAILURES_PREFIX}{username}", f"{LOGIN_WAIT_PREFIX}{username}"

    async def admit(self, username: str) -> LoginAdmission:
        admitted, wait = await self._redis.eval(  # type: ignore[misc]
            _LOGIN_ADMIT_SCRIPT,
            2,
            *self._keys(username),
            LOGIN_FAILURE_WINDOW_SECONDS,
            LOGIN_FREE_FAILURES,
            LOGIN_FIRST_WAIT_SECONDS,
            LOGIN_MAX_WAIT_SECONDS,
        )
        return LoginAdmission(admitted=admitted == 1, wait_seconds=int(wait))

    async def clear(self, username: str) -> None:
        await self._redis.delete(*self._keys(username))


class ClientFailureBudget:
    """Failures one client address may make at one credential step.

    ``client`` is the address ``resolved_client_address`` gives, and None
    where the server cannot tell clients apart; with None nothing is counted
    or refused, since every client would share one budget.

    Counts failures, not attempts: the slot is taken before the credential
    is checked, as everywhere here, and handed back when it holds. Handed
    back, never cleared: clearing on success would let anyone reset their
    address's count by signing in to an account of their own.
    """

    def __init__(self, redis: Redis, step: str) -> None:
        self._redis = redis
        self._step = step

    def _key(self, client: str) -> str:
        return f"{CLIENT_FAILURE_PREFIX}{self._step}:{client}"

    async def spend(self, client: str | None) -> int:
        """Take a slot. 0 when taken; otherwise the seconds until the window
        ends, and the attempt must be refused."""
        if client is None:
            return 0
        wait = await self._redis.eval(  # type: ignore[misc]
            _CLIENT_SPEND_SCRIPT,
            1,
            self._key(client),
            CLIENT_MAX_FAILURES,
            CLIENT_FAILURE_WINDOW_SECONDS,
        )
        return int(wait)

    async def refund(self, client: str | None) -> None:
        """Hand back a slot whose attempt did not fail."""
        if client is None:
            return
        await self._redis.eval(_CLIENT_REFUND_SCRIPT, 1, self._key(client))  # type: ignore[misc]


class AttemptLimiter:
    """Failed-attempt budget for one credential step, keyed by ``subject``,
    that locks the step once spent.

    Only for steps a stranger cannot reach: each subclass is keyed by a user
    id and sits behind a correct password or a live session, so whoever can
    lock it already holds one of those. Which key a step uses is part of what
    makes it a *separate* budget, so it is the subclass's choice.
    """

    _attempts_prefix: str
    _lockout_prefix: str
    _max_attempts: int
    _what: str

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


class TwoFactorRateLimiter(AttemptLimiter):
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


class BackupCodeRateLimiter(AttemptLimiter):
    """Budget for backup-code guesses only, keyed by ``str(user_id)``.

    Stacked *under* TwoFactorRateLimiter, not instead of it: a backup-code
    attempt spends both budgets, a TOTP attempt spends only the 2FA one.
    """

    _attempts_prefix = BACKUP_CODE_ATTEMPTS_PREFIX
    _lockout_prefix = BACKUP_CODE_LOCKOUT_PREFIX
    _max_attempts = MAX_BACKUP_CODE_ATTEMPTS
    _what = "2fa backup code"


class StepUpTwoFactorRateLimiter(AttemptLimiter):
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


class StepUpPasswordRateLimiter(AttemptLimiter):
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
