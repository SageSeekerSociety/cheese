"""users 路由包共用的请求模型、依赖提供者与私有辅助。

从原来的单文件 users.py 头部与各段之间原样搬来，函数体逐行未改。
"""

import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import (
    Request,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.users_common import (
    TRUST_COOKIE,
)
from app.common.auth import (
    SudoPurpose,
)
from app.core.config import settings
from app.core.email import is_placeholder_email
from app.core.errors import (
    AuthenticationRequiredError,
    ForbiddenError,
    InternalServerError,
    UnprocessableEntityError,
)
from app.core.sentences import say
from app.domain.legal.documents import check_current
from app.domain.legal.services import CONSENT_METHODS
from app.domain.user.models import (
    UserTrustedDevice,
)
from app.domain.user.trusted_devices import TrustedDeviceService

if TYPE_CHECKING:
    pass

# ── Request Models ────────────────────────────────────────────────────────────


class SendEmailCodeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    email: str = Field(..., min_length=1)
    invite_code: str | None = Field(default=None, alias="inviteCode")


class AddEmailCodeRequest(BaseModel):
    email: str = Field(..., min_length=1)


class AddEmailRequest(AddEmailCodeRequest):
    code: str = Field(..., min_length=1)


class OAuthEmailCodeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    state_token: str = Field(..., alias="stateToken", min_length=1)
    email: str = Field(..., min_length=1)


class OAuthEmailVerifyRequest(OAuthEmailCodeRequest):
    code: str = Field(..., min_length=1)


class SignupConsent(BaseModel):
    #: document key → the version shown on the signup page.
    documents: dict[str, str]
    method: str


class RegisterUserRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    username: str = Field(..., min_length=1)
    nickname: str = Field(..., min_length=1)
    email: str = Field(..., min_length=1)
    email_code: str = Field(..., alias="emailCode", min_length=1)
    password: str
    invite_code: str | None = Field(default=None, alias="inviteCode")
    consent: SignupConsent | None = None


class LoginRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    totp_code: str | None = Field(default=None, alias="totpCode")


class SudoAuthRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    # None asks for a ticket on the strength of the session's sudo window
    # alone, proving nothing new.
    method: str | None = None
    credentials: dict = Field(default_factory=dict)
    # Which privileged operation the resulting ticket may be spent on. Absent
    # for the operations still gated in the client alone: they redeem nothing,
    # so minting them a ticket would only put an unusable credential on the
    # wire. See ``SudoPurpose``.
    purpose: SudoPurpose | None = None


class TwoFactorCodeRequest(BaseModel):
    code: str = Field(..., min_length=1)


class EmailCodeSignInRequest(BaseModel):
    email: str = Field(..., min_length=1)


class EmailCodeSignInVerifyRequest(BaseModel):
    email: str = Field(..., min_length=1)
    code: str = Field(..., min_length=1)


class CreateInviteCodeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    max_uses: int = Field(default=1, alias="maxUses")
    note: str | None = None


async def _trusted_device(
    request: Request, db: AsyncSession, user_id: int
) -> UserTrustedDevice | None:
    """The live trust this browser holds for ``user_id``, if any. Only the
    server decides: a cookie for another account, or one revoked or expired,
    finds nothing."""
    return await TrustedDeviceService(db).find(
        request.cookies.get(TRUST_COOKIE), user_id
    )


logger = logging.getLogger(__name__)


# Namespaces the single-use reservations that make a 2FA ticket redeemable
# exactly once (#357).
_PENDING_2FA_SCOPE = "2fa_pending"


async def _issue_2fa_pending_token(
    user_id: int, *, expires_at: int | None = None
) -> str:
    """Mint a 2FA ticket and reserve it, or refuse the login step outright.

    Fail-closed on purpose: a ticket we could not reserve is one the verify
    step will refuse anyway (its claim finds no reservation), so handing it
    out would only turn a 500 here into a mystifying "invalid session token"
    one screen later. ``single_use_state`` raises when Redis is unreachable,
    and Redis being unreachable already means no 2FA login can complete —
    the TOTP secret lives there too.

    ``expires_at`` re-issues against an existing deadline (see
    ``mint_2fa_pending_token``); the reservation is sized to match, so the
    key dies with the ticket rather than outliving it.
    """
    from app.common.auth import PENDING_2FA_TTL_S, mint_2fa_pending_token
    from app.core.single_use_state import SingleUseUnavailableError, reserve

    if expires_at is None:
        ttl_s = PENDING_2FA_TTL_S
    else:
        ttl_s = expires_at - int(datetime.now(UTC).timestamp())
        if ttl_s <= 0:
            raise AuthenticationRequiredError(
                "2FA session expired, please sign in again",
                {"reason": "session_expired"},
            )

    minted = mint_2fa_pending_token(user_id, expires_at=expires_at)
    try:
        await reserve(_PENDING_2FA_SCOPE, minted.jti, ttl_s=ttl_s)
    except SingleUseUnavailableError:
        logger.exception("2fa: cannot reserve pending ticket uid=%s", user_id)
        raise InternalServerError(say("twoFactorUnavailable")) from None
    return minted.token


def _normalize_registration_invite_code(
    invite_code: str | None, *, required: bool
) -> str | None:
    """Return the required normalized code, or ignore it in open-registration mode."""
    if not required:
        return None
    normalized = (invite_code or "").strip()
    if not normalized:
        raise UnprocessableEntityError("Invite code is required")
    return normalized


def _signup_consent(documents: dict[str, str] | None, method: str | None) -> str | None:
    """Why this signup's consent cannot be accepted, or None when it can.

    An account is only ever created with a recorded consent to the current
    version of every document (#1486); a page opened before a newer version
    was published is sent back to be reread rather than recorded against
    text the person never saw."""
    if not documents or method not in CONSENT_METHODS:
        return say("consentRequired")
    try:
        check_current(documents)
    except ValueError as exc:
        return say("consentStale" if str(exc) == "CONSENT_STALE" else "consentRequired")
    return None


def _invite_code_error(exc: ValueError) -> UnprocessableEntityError:
    error_map = {
        "INVALID_CODE": "Invalid invite code",
        "CODE_DISABLED": "This invite code has been disabled",
        "CODE_EXPIRED": "This invite code has expired",
        "CODE_EXHAUSTED": "This invite code has been fully used",
    }
    return UnprocessableEntityError(error_map.get(str(exc), "Invalid invite code"))


def _own_email(raw: str) -> str:
    """An address an account may hold: well formed, and one mail can reach."""
    email = raw.strip()
    if not _EMAIL_FORMAT.match(email) or is_placeholder_email(email):
        raise UnprocessableEntityError(
            "Invalid email address format", {"reason": "invalid_email"}
        )
    return email


async def _send_email_code(request: Request, email: str) -> None:
    """Mail a code proving ownership of ``email``: the sign-up code, with its
    per-address quota, the site's mail allowance and its limit on wrong
    guesses."""
    from redis.asyncio import Redis as AsyncRedis

    from app.core.client_address import resolved_client_address
    from app.domain.user.verification_service import EmailVerificationService

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        await EmailVerificationService(redis).send_verification_code(
            email.lower(), resolved_client_address(request)
        )
    finally:
        await redis.aclose()


async def _check_email_code(request: Request, email: str, code: str) -> None:
    """Spend ``code`` against ``email``, counted like the sign-up check."""
    from redis.asyncio import Redis as AsyncRedis

    from app.core.client_address import resolved_client_address
    from app.domain.user.login_security import ClientFailureBudget
    from app.domain.user.verification_service import EmailVerificationService

    client = resolved_client_address(request)
    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        budget = ClientFailureBudget(redis, "email_code")
        wait = await budget.spend(client)
        if wait:
            raise _too_many_from_client(wait)
        if not await EmailVerificationService(redis).verify_code(
            email.lower(), code.strip()
        ):
            raise UnprocessableEntityError(
                "Invalid or expired verification code",
                {"reason": "invalid_email_code"},
            )
        await budget.refund(client)
    finally:
        await redis.aclose()


def _email_code_confirms(email: str | None, *, two_factor: bool) -> bool:
    """Whether a code mailed to the account confirms its identity.

    Not with two-step verification: the mailbox alone would then be enough to
    turn it off. Not for a placeholder address, which nobody reads.
    """

    return not two_factor and not is_placeholder_email(email)


def _email_code_unavailable() -> ForbiddenError:
    return ForbiddenError(
        "An email code cannot confirm this account's identity",
        {"reason": "email_code_unavailable"},
    )


def _too_many_from_client(wait_seconds: int) -> ForbiddenError:
    return ForbiddenError(
        "Too many failed attempts from this network. "
        f"Try again in {wait_seconds} seconds",
        {"reason": "too_many_attempts", "retryAfterSeconds": wait_seconds},
    )


_EMAIL_FORMAT = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
