import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Any
from urllib.parse import urlsplit

import jwt
from fastapi import (
    APIRouter,
    Body,
    Depends,
    Form,
    Path,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.legal import client_context
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.common.auth import (
    SudoPurpose,
    create_access_token,
    get_current_session_id,
)
from app.core.config import GATEWAY_MOUNT, settings
from app.core.email import is_placeholder_email
from app.core.errors import (
    AuthenticationRequiredError,
    BadRequestError,
    ConflictError,
    ForbiddenError,
    InternalServerError,
    NotFoundError,
    SudoRequiredError,
    UnprocessableEntityError,
)
from app.db.session import get_db
from app.domain.answers.repositories import AnswerRepository
from app.domain.identity.handles import is_reserved_username
from app.domain.invite.services import InviteCodeService
from app.domain.legal.documents import check_current
from app.domain.legal.services import CONSENT_METHODS, ConsentService
from app.domain.oauth.repositories import OAuthConnectionRepository
from app.domain.oauth.services import OAuthService
from app.domain.passkey.prompt import PasskeyPromptService
from app.domain.passkey.repositories import PasskeyRepository
from app.domain.passkey.services import PasskeyService
from app.domain.questions.repositories import (
    QuestionRepository,
    QuestionTopicRepository,
)
from app.domain.space.services import SpaceLabels
from app.domain.team.membership_services import TeamMembershipService
from app.domain.team.repositories import (
    TeamMembershipApplicationRepository,
    TeamRepository,
)
from app.domain.team.services import TeamService
from app.domain.user.models import (
    UserFollowingRelationship,
    UserSession,
    UserTrustedDevice,
)
from app.domain.user.realname_services import UserRealNameService
from app.domain.user.repositories import (
    UserFollowingRepository,
    UserProfileRepository,
    UserRealNameRepository,
    UserRepository,
    UserStatisticsRepository,
)
from app.domain.user.services import (
    NICKNAME_MAX_LENGTH,
    USERNAME_MAX_LENGTH,
    UserAuthService,
    UserProfileService,
    is_valid_username,
    lookup_account,
    normalize_nickname,
)
from app.domain.user.sessions import RevokeReason, SessionService
from app.domain.user.trusted_devices import Granted, TrustedDeviceService

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from app.domain.user.login_security import LoginDelay

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


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    password: str
    sudo_ticket: str | None = Field(default=None, alias="sudoTicket")


class SudoTicketRequest(BaseModel):
    """The body of an operation that redeems a sudo ticket and needs nothing
    else."""

    model_config = ConfigDict(populate_by_name=True)

    sudo_ticket: str | None = Field(default=None, alias="sudoTicket")


class PutUserIdentityRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    real_name: str = Field(default="", alias="realName")
    student_id: str = Field(default="", alias="studentId")
    grade: str = ""
    major: str = ""
    class_name: str = Field(default="", alias="className")
    sudo_ticket: str | None = Field(default=None, alias="sudoTicket")


class PatchUserIdentityRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    real_name: str | None = Field(default=None, alias="realName")
    student_id: str | None = Field(default=None, alias="studentId")
    grade: str | None = None
    major: str | None = None
    class_name: str | None = Field(default=None, alias="className")
    sudo_ticket: str | None = Field(default=None, alias="sudoTicket")


class TwoFactorCodeRequest(BaseModel):
    code: str = Field(..., min_length=1)


class ForgotPasswordRequest(BaseModel):
    email: str = Field(..., min_length=1)


class EmailCodeSignInRequest(BaseModel):
    email: str = Field(..., min_length=1)


class EmailCodeSignInVerifyRequest(BaseModel):
    email: str = Field(..., min_length=1)
    code: str = Field(..., min_length=1)


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    token: str = Field(..., min_length=1)
    password: str


class CreateInviteCodeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    max_uses: int = Field(default=1, alias="maxUses")
    note: str | None = None


router = APIRouter(prefix="/users", tags=["Users"])


@router.get("/lookup", summary="Find one account by exact username or email")
async def lookup_account_route(
    q: str = Query(..., min_length=1, max_length=254),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    """The one person an external-member invitation is about to go to.

    Exact username or email only, like finding an external contact: no partial
    match, so the endpoint cannot be used to list who is registered.
    """
    _ = auth_user
    found = await lookup_account(db, q)
    if found is None:
        raise NotFoundError("No account with that username or email")
    return {"code": 200, "message": "OK", "data": found}


# The refresh token rides in this cookie and is sent to the auth routes only.
# Its path is the one the browser sees, through the gateway, not the route's.
REFRESH_COOKIE = "cheese_refresh"
_REFRESH_COOKIE_PATH = f"{GATEWAY_MOUNT}/users/auth"


def _set_refresh_cookie(
    response: Response, refresh_token: str, expires_at: datetime
) -> None:
    """The cookie lasts as long as the sign-in behind it. Without a Max-Age
    the browser drops it when the session ends while the access token in
    localStorage survives, and the next refresh signs the user out."""
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        max_age=max(0, int((expires_at - datetime.now(UTC)).total_seconds())),
        httponly=True,
        secure=settings.environment not in ("development", "test"),
        samesite="lax",
        path=_REFRESH_COOKIE_PATH,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        REFRESH_COOKIE,
        httponly=True,
        secure=settings.environment not in ("development", "test"),
        samesite="lax",
        path=_REFRESH_COOKIE_PATH,
    )


# A browser trusted to skip two-step verification holds this cookie. Scoped
# like the refresh cookie: only the sign-in routes ever read it.
TRUST_COOKIE = "cheese_trusted_device"


def _set_trust_cookie(response: Response, granted: Granted) -> None:
    response.set_cookie(
        TRUST_COOKIE,
        granted.token,
        max_age=max(0, int((granted.expires_at - datetime.now(UTC)).total_seconds())),
        httponly=True,
        secure=settings.environment not in ("development", "test"),
        samesite="lax",
        path=_REFRESH_COOKIE_PATH,
    )


async def _trusted_device(
    request: Request, db: AsyncSession, user_id: int
) -> UserTrustedDevice | None:
    """The live trust this browser holds for ``user_id``, if any. Only the
    server decides: a cookie for another account, or one revoked or expired,
    finds nothing."""
    return await TrustedDeviceService(db).find(
        request.cookies.get(TRUST_COOKIE), user_id
    )


def _origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


def _require_same_origin(request: Request) -> None:
    """Refuse a refresh or sign-out that another site had the browser send.

    SameSite=Lax keeps the cookie off a cross-site POST, but not off one from
    a sibling subdomain, and older browsers do not apply it at all. Modern
    browsers say where a request came from in Sec-Fetch-Site; older ones at
    least send Origin on a POST. A request with neither did not come from a
    browser, so it carries no cookie it did not mean to, and passes.
    """
    site = request.headers.get("sec-fetch-site")
    if site is not None:
        if site != "same-origin":
            raise ForbiddenError("Cross-site request refused")
        return
    origin = request.headers.get("origin")
    if origin is None:
        return
    trusted = {
        _origin_of(settings.frontend_url),
        *map(_origin_of, settings.cors_origins),
    }
    same_host = urlsplit(origin).netloc == request.headers.get("host")
    if not same_host and _origin_of(origin) not in trusted:
        raise ForbiddenError("Cross-site request refused")


# The credentials ``/auth/sudo`` accepts from any account that has them. A
# mailed code is one only without two-step verification, which is why an
# email-code sign-in that a trusted device let past the second step is not.
_SUDO_SIGN_IN_METHODS = frozenset({"passkey", "password", "totp"})


def _sign_in_opens_sudo(login_method: str, two_factor_skipped: bool) -> bool:
    """Whether this sign-in proved a credential sudo would have accepted, so
    that asking for one again straight away would only repeat it."""
    if login_method in _SUDO_SIGN_IN_METHODS:
        return True
    return login_method == "email_code" and not two_factor_skipped


async def issue_session(
    response: Response,
    request: Request,
    db: AsyncSession,
    *,
    user_id: int,
    handle: str,
    login_method: str,
    trust: UserTrustedDevice | None = None,
    grant_trust: bool = False,
) -> str:
    """Sign the user in: open a session, put its refresh token in the cookie
    on ``response``, and return the access token for the body.

    Every way of signing in ends here, so every sign-in is a session the
    account can see and end.

    ``trust`` is the trusted device that stood in for two-step verification,
    when one did. ``grant_trust`` trusts this browser from now on: the
    sign-in has just passed two-step verification and its owner asked not to
    be asked again here.
    """
    from app.core.client_address import resolved_client_address

    user_agent = request.headers.get("user-agent", "")
    # Behind a proxy that is not trusted to name the client, the peer is the
    # proxy; the device list shows no address rather than the proxy's.
    started = await SessionService(db).start(
        user_id,
        login_method,
        ip=resolved_client_address(request) or "",
        user_agent=user_agent,
        two_factor_skipped=trust is not None,
        sudo=_sign_in_opens_sudo(login_method, trust is not None),
    )
    trusted = TrustedDeviceService(db)
    if trust is not None:
        await trusted.used(trust, started.session_id)
    if grant_trust:
        _set_trust_cookie(
            response,
            await trusted.grant(
                user_id,
                started.session_id,
                user_agent=user_agent,
                replacing=request.cookies.get(TRUST_COOKIE),
            ),
        )
    _set_refresh_cookie(response, started.refresh_token, started.expires_at)
    return create_access_token(user_id, handle=handle, sid=started.session_id)


logger = logging.getLogger(__name__)

# Namespaces the single-use reservations that make a 2FA ticket redeemable
# exactly once (#357).
_PENDING_2FA_SCOPE = "2fa_pending"

# Namespaces the reservations behind a sudo ticket. Its own scope, so a ticket
# can never be redeemed by whatever else happens to hold a matching ``jti``.
_SUDO_TICKET_SCOPE = "sudo_ticket"


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
        raise InternalServerError("暂时无法完成两步验证，请稍后重试") from None
    return minted.token


async def _spend_2fa_attempt(
    redis: "Redis",
    user_id: int,
    verify: Callable[[], Awaitable[bool]],
    *,
    step_up: bool = False,
    is_backup_code: bool = False,
    reissue_until: int | None = None,
    client: str | None = None,
) -> None:
    """Check a second-factor code against a per-user attempt budget (#357).

    Every path that validates a second factor must go through here, including
    the ones that take a code inline and never mint a ticket — otherwise the
    un-budgeted path is the whole vulnerability, unchanged.

    ``step_up`` picks the budget for re-proving 2FA inside a live session
    (#389) instead of the login one. Which budget an entrance draws on is the
    security question, not a detail: put a stolen session's guesses on the
    login counter and grinding sudo would lock the owner out of signing in,
    which is a denial of service dressed as a rate limit.

    Raises on a wrong code (or an exhausted budget) and returns None on a
    good one, so callers cannot forget to check a boolean.

    ``reissue_until`` hands a replacement ticket back with the rejection, so
    a typo does not cost a whole password round trip. It replaces the *ticket*
    and nothing else: the budget below is untouched by re-issuing, which is
    the point — tickets are what bound fan-out, the user counter is what
    bounds volume, and confusing the two would quietly restore the bug. The
    replacement inherits ``reissue_until`` as its deadline rather than
    starting a new one, so wrong guesses cannot extend the half-authenticated
    window.

    ``client`` also counts a wrong code against the client's address (see
    ``ClientFailureBudget``); the login entrances pass it, step-up does not,
    being reachable only from a live session.

    Every rejection carries a machine-readable ``reason``. "Wrong code, try
    again here", "your session died, go sign in" and "you are locked out for
    fifteen minutes" need three different things from the user, and a client
    that cannot tell them apart will sit there retrying the impossible one.
    """
    from app.domain.user.login_security import (
        LOCKOUT_DURATION_SECONDS,
        AttemptLimiter,
        BackupCodeRateLimiter,
        ClientFailureBudget,
        StepUpTwoFactorRateLimiter,
        TwoFactorRateLimiter,
    )

    subject = str(user_id)
    # Exactly one of the two TOTP budgets, plus the backup-code one when the
    # code is a backup code — so a backup guess spends both and routine TOTP
    # typos cannot exhaust the tighter backup allowance.
    limiters: list[AttemptLimiter] = [
        StepUpTwoFactorRateLimiter(redis) if step_up else TwoFactorRateLimiter(redis)
    ]
    if is_backup_code:
        limiters.append(BackupCodeRateLimiter(redis))

    client_budget = ClientFailureBudget(redis, "2fa")
    wait = await client_budget.spend(client)
    if wait:
        raise _too_many_from_client(wait)
    budget: int | None = None
    wrong = False
    try:
        for limiter in limiters:
            if await limiter.is_locked_out(subject):
                remaining = await limiter.get_remaining_lockout_seconds(subject)
                raise ForbiddenError(
                    f"Too many 2FA attempts. Try again in {remaining} seconds",
                    {"reason": "too_many_attempts", "retryAfterSeconds": remaining},
                )

        for limiter in limiters:
            left = await limiter.consume_attempt(subject)
            if left is None:
                raise ForbiddenError(
                    "Too many 2FA attempts. Try again later",
                    {
                        "reason": "too_many_attempts",
                        "retryAfterSeconds": LOCKOUT_DURATION_SECONDS,
                    },
                )
            budget = left if budget is None else min(budget, left)

        wrong = not await verify()
    finally:
        if not wrong:
            await client_budget.refund(client)

    if wrong:
        if budget == 0:
            raise ForbiddenError(
                "Too many failed 2FA attempts. Account locked for 15 minutes",
                {
                    "reason": "too_many_attempts",
                    "retryAfterSeconds": LOCKOUT_DURATION_SECONDS,
                },
            )
        rejection: dict[str, Any] = {
            "reason": "invalid_code",
            "attemptsRemaining": budget,
        }
        if reissue_until is not None:
            rejection["tempToken"] = await _issue_2fa_pending_token(
                user_id, expires_at=reissue_until
            )
        raise AuthenticationRequiredError(
            f"Invalid 2FA code. {budget} attempts remaining", rejection
        )

    for limiter in limiters:
        await limiter.clear_attempts(subject)


async def _spend_sudo_password_attempt(
    redis: "Redis",
    user_id: int,
    verify: Callable[[], Awaitable[bool]],
    *,
    message: str,
) -> None:
    """Check a password re-proof against the step-up password budget (#389).

    ``/auth/sudo`` is reachable with nothing but a live session, so before
    this existed a stolen cookie bought unlimited password guesses against an
    account whose owner never sees a login-failure counter move. The slot is
    spent before the comparison, for the same check-then-act reason
    ``consume_attempt`` documents.
    """
    from app.domain.user.login_security import (
        LOCKOUT_DURATION_SECONDS,
        StepUpPasswordRateLimiter,
    )

    limiter = StepUpPasswordRateLimiter(redis)
    subject = str(user_id)

    if await limiter.is_locked_out(subject):
        remaining = await limiter.get_remaining_lockout_seconds(subject)
        raise ForbiddenError(
            f"Too many attempts. Try again in {remaining} seconds",
            {"reason": "too_many_attempts", "retryAfterSeconds": remaining},
        )

    budget = await limiter.consume_attempt(subject)
    if budget is None:
        raise ForbiddenError(
            "Too many attempts. Try again later",
            {
                "reason": "too_many_attempts",
                "retryAfterSeconds": LOCKOUT_DURATION_SECONDS,
            },
        )

    if not await verify():
        if budget == 0:
            raise ForbiddenError(
                "Too many failed attempts. Locked for 15 minutes",
                {
                    "reason": "too_many_attempts",
                    "retryAfterSeconds": LOCKOUT_DURATION_SECONDS,
                },
            )
        raise AuthenticationRequiredError(
            f"{message}. {budget} attempts remaining",
            {"reason": "invalid_credentials", "attemptsRemaining": budget},
        )

    await limiter.clear_attempts(subject)


async def _issue_sudo_ticket(user_id: int, purpose: SudoPurpose) -> str:
    """Mint a sudo ticket and reserve it, or refuse the re-authentication.

    Fail-closed for the same reason as the 2FA pending ticket: a ticket we
    could not reserve is one the operation will refuse anyway, so handing it
    out turns an error here into a baffling "verify again" a screen later.
    """
    from app.common.auth import SUDO_TICKET_TTL_S, mint_sudo_ticket
    from app.core.single_use_state import SingleUseUnavailableError, reserve

    minted = mint_sudo_ticket(user_id, purpose)
    try:
        await reserve(_SUDO_TICKET_SCOPE, minted.jti, ttl_s=SUDO_TICKET_TTL_S)
    except SingleUseUnavailableError:
        logger.exception("sudo: cannot reserve ticket uid=%s", user_id)
        raise InternalServerError("暂时无法完成安全验证，请稍后重试") from None
    return minted.token


async def _passkey_enrollment(user_id: int, session: AsyncSession) -> dict[str, Any]:
    """What a finished sign-in hands back for adding a passkey.

    The person has just proved more than the sudo page would ask of them, so
    sending them there before a passkey can be added would only make them
    prove it twice. The ticket is an ordinary sudo ticket for ``PASSKEY_ADD``:
    the same few minutes, the same single use, good for nothing else. It
    travels in the response body and never in a URL.

    ``offer`` says whether this account is due the screen offering a passkey
    (it has none, and has not declined recently); the client adds what only
    it can know, such as where the sign-in is headed.
    """
    prompt = await PasskeyPromptService(session).state(user_id)
    return {
        "ticket": await _issue_sudo_ticket(user_id, SudoPurpose.PASSKEY_ADD),
        "offer": prompt.due,
        "canStopAsking": prompt.can_stop_asking,
    }


async def _spend_sudo_ticket(
    ticket: object, *, user_id: int, purpose: SudoPurpose
) -> None:
    """Redeem a sudo ticket for exactly this user and this operation, once.

    Every refusal is the same ``SudoRequiredError``, because every refusal has
    the same remedy — re-authenticate and try again — and because saying which
    of the four checks failed would tell a holder of a stolen session whether
    a captured ticket was expired, already spent, or simply for something
    else.

    Fail-closed when Redis is unreachable: without the reservation there is no
    way to tell a first use from a replay, and "cannot tell" is not "allow".
    """
    from app.common.auth import verify_sudo_ticket
    from app.core.single_use_state import SingleUseUnavailableError, claim

    claims = verify_sudo_ticket(ticket) if isinstance(ticket, str) and ticket else None
    if claims is None or claims.user_id != user_id or claims.purpose != purpose:
        raise SudoRequiredError("Re-authentication required for this operation")

    try:
        spent = await claim(_SUDO_TICKET_SCOPE, claims.jti)
    except SingleUseUnavailableError:
        logger.exception("sudo: cannot claim ticket uid=%s", user_id)
        raise InternalServerError("暂时无法完成安全验证，请稍后重试") from None
    if not spent:
        raise SudoRequiredError("Re-authentication required for this operation")


def _reject_overlong_password(password: str) -> None:
    """Refused before anything is consumed: bcrypt cannot hash it, and failing
    after the email code or reset token is spent would cost the user both."""
    from app.domain.user.passwords import MAX_PASSWORD_BYTES, password_too_long

    if password_too_long(password):
        raise BadRequestError(f"Password must not exceed {MAX_PASSWORD_BYTES} bytes")


# At least 8 characters, a letter, a digit and an ASCII symbol: the web
# client's rule (REGEX_PASSWORD), so the form and the server agree on it.
_NEW_PASSWORD_PATTERN = re.compile(
    r"^(?=.*[a-zA-Z])(?=.*\d)(?=.*[\x00-\x2F\x3A-\x40\x5B-\x60\x7B-\x7F]).{8,}$"
)


def _require_new_password(password: str) -> None:
    """The rule a password chosen for an account must meet, and the length
    bcrypt can hold. Checked before anything single-use is spent."""
    if not _NEW_PASSWORD_PATTERN.match(password):
        raise UnprocessableEntityError(
            "Use at least 8 characters, with a letter, a number, "
            "and a special character"
        )
    _reject_overlong_password(password)


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
        return "请阅读并同意《用户协议》和《隐私政策》"
    try:
        check_current(documents)
    except ValueError as exc:
        if str(exc) == "CONSENT_STALE":
            return "协议已更新，请刷新页面后重新阅读并同意"
        return "请阅读并同意《用户协议》和《隐私政策》"
    return None


def _invite_code_error(exc: ValueError) -> UnprocessableEntityError:
    error_map = {
        "INVALID_CODE": "Invalid invite code",
        "CODE_DISABLED": "This invite code has been disabled",
        "CODE_EXPIRED": "This invite code has expired",
        "CODE_EXHAUSTED": "This invite code has been fully used",
    }
    return UnprocessableEntityError(error_map.get(str(exc), "Invalid invite code"))


async def get_user_auth_service(
    db=Depends(get_db),
) -> UserAuthService:
    user_repo = UserRepository(session=db)
    profile_repo = UserProfileRepository(session=db)
    follow_repo = UserFollowingRepository(session=db)
    stats_repo = UserStatisticsRepository(session=db)
    return UserAuthService(
        user_repo=user_repo,
        profile_repo=profile_repo,
        follow_repo=follow_repo,
        stats_repo=stats_repo,
    )


async def get_user_profile_service(
    db=Depends(get_db),
) -> UserProfileService:
    profile_repo = UserProfileRepository(session=db)
    return UserProfileService(profile_repo=profile_repo)


async def get_team_membership_service(
    db=Depends(get_db),
) -> TeamMembershipService:
    team_repo = TeamRepository(session=db)
    app_repo = TeamMembershipApplicationRepository(session=db)
    return TeamMembershipService(
        session=db, team_repo=team_repo, application_repo=app_repo
    )


async def get_user_realname_service(
    db=Depends(get_db),
) -> UserRealNameService:
    user_repo = UserRepository(session=db)
    profile_repo = UserProfileRepository(session=db)
    realname_repo = UserRealNameRepository(session=db)
    return UserRealNameService(
        session=db,
        user_repo=user_repo,
        profile_repo=profile_repo,
        realname_repo=realname_repo,
        space_labels=SpaceLabels(session=db),
    )


async def get_passkey_service(
    db=Depends(get_db),
) -> PasskeyService:
    repo = PasskeyRepository(session=db)
    return PasskeyService(repo=repo)


async def get_oauth_service(
    db=Depends(get_db),
) -> OAuthService:
    return OAuthService(repo=OAuthConnectionRepository(session=db))


@router.post(
    "/{userId}/followers",
    summary="Follow user",
    status_code=201,
)
async def follow_user(
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    """Follow another user.

    NOTE: 当前实现不引入完整的权限模型，只做基础合法性检查：
    - 不能关注自己；
    - 要求被关注用户存在；
    - 重复关注直接视为错误返回 400。
    """
    if auth_user.user_id == user_id:
        raise UnprocessableEntityError("Cannot follow yourself")

    user_repo = UserRepository(session=db)
    follow_repo = UserFollowingRepository(session=db)

    target = await user_repo.get_by_id(user_id)
    if target is None:
        raise NotFoundError("User not found")

    if await follow_repo.is_following(auth_user.user_id, user_id):
        raise UnprocessableEntityError("User already followed")

    await follow_repo.add_follow(auth_user.user_id, user_id)
    follow_count = await follow_repo.count_following(auth_user.user_id)

    return {
        "code": 201,
        "message": "Follow user successfully.",
        "data": {
            "follow_count": follow_count,
        },
    }


@router.delete(
    "/me/team-requests/{requestId}",
    summary="Cancel my pending join request",
)
async def cancel_my_join_request(
    request_id: Annotated[int, Path(ge=1, alias="requestId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    membership_service: TeamMembershipService = Depends(get_team_membership_service),
) -> Response:
    await membership_service.cancel_my_join_request(
        user_id=auth_user.user_id, request_id=request_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/me/teams/{teamId}",
    summary="Leave Team",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def leave_team(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> None:
    team_repo = TeamRepository(session=db)
    team_service = TeamService(team_repo)
    await team_service.remove_team_member(
        team_id=team_id,
        target_user_id=auth_user.user_id,
        actor_user_id=auth_user.user_id,
    )


@router.post(
    "/me/team-invitations/{invitationId}/accept",
    summary="Accept a team invitation",
)
async def accept_team_invitation(
    invitation_id: Annotated[int, Path(ge=1, alias="invitationId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    membership_service: TeamMembershipService = Depends(get_team_membership_service),
) -> Response:
    await membership_service.accept_team_invitation(
        user_id=auth_user.user_id, invitation_id=invitation_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/me/team-invitations/{invitationId}/decline",
    summary="Decline a team invitation",
)
async def decline_team_invitation(
    invitation_id: Annotated[int, Path(ge=1, alias="invitationId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    membership_service: TeamMembershipService = Depends(get_team_membership_service),
) -> Response:
    await membership_service.decline_team_invitation(
        user_id=auth_user.user_id, invitation_id=invitation_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/me/team-requests",
    summary="List my team join requests",
)
async def list_my_team_requests(
    status: str | None = Query(default=None),
    pageStart: int | None = Query(default=None),
    pageSize: int | None = Query(default=None),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    membership_service: TeamMembershipService = Depends(get_team_membership_service),
    db=Depends(get_db),
) -> dict:
    from app.api.routes.teams import _application_to_api_model, _load_application_maps

    status_enum = None
    if status is not None:
        upper = status.upper()
        if upper in {
            "PENDING",
            "APPROVED",
            "REJECTED",
            "ACCEPTED",
            "DECLINED",
            "CANCELED",
        }:
            from app.domain.team.models import ApplicationStatus

            status_enum = ApplicationStatus[upper]
        else:
            raise BadRequestError(f"Invalid status: {status}")
    apps, page = await membership_service.list_my_join_requests(
        user_id=auth_user.user_id,
        status=status_enum,
        page_start=pageStart,
        page_size=pageSize,
    )
    users_map, profiles_map, teams_map = await _load_application_maps(db, apps)
    items = [
        _application_to_api_model(
            app, users_map=users_map, profiles_map=profiles_map, teams_map=teams_map
        )
        for app in apps
    ]
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "requests": items,
            "page": page,
        },
    }


@router.get(
    "/me/team-invitations",
    summary="List my team invitations",
)
async def list_my_team_invitations(
    status: str | None = Query(default=None),
    pageStart: int | None = Query(default=None),
    pageSize: int | None = Query(default=None),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    membership_service: TeamMembershipService = Depends(get_team_membership_service),
    db=Depends(get_db),
) -> dict:
    from app.api.routes.teams import _application_to_api_model, _load_application_maps

    status_enum = None
    if status is not None:
        upper = status.upper()
        if upper in {
            "PENDING",
            "APPROVED",
            "REJECTED",
            "ACCEPTED",
            "DECLINED",
            "CANCELED",
        }:
            from app.domain.team.models import ApplicationStatus

            status_enum = ApplicationStatus[upper]
        else:
            raise BadRequestError(f"Invalid status: {status}")
    apps, page = await membership_service.list_my_invitations(
        user_id=auth_user.user_id,
        status=status_enum,
        page_start=pageStart,
        page_size=pageSize,
    )
    users_map, profiles_map, teams_map = await _load_application_maps(db, apps)
    items = [
        _application_to_api_model(
            app, users_map=users_map, profiles_map=profiles_map, teams_map=teams_map
        )
        for app in apps
    ]
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "invitations": items,
            "page": page,
        },
    }


@router.delete(
    "/{userId}/followers",
    summary="Unfollow user",
)
async def unfollow_user(
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    """Unfollow a previously followed user."""
    if auth_user.user_id == user_id:
        raise UnprocessableEntityError("Cannot unfollow yourself")

    user_repo = UserRepository(session=db)
    follow_repo = UserFollowingRepository(session=db)

    target = await user_repo.get_by_id(user_id)
    if target is None:
        raise NotFoundError("User not found")

    removed = await follow_repo.soft_delete_follow(auth_user.user_id, user_id)
    if not removed:
        raise UnprocessableEntityError("User not followed yet")

    follow_count = await follow_repo.count_following(auth_user.user_id)
    return {
        "code": 200,
        "message": "Unfollow user successfully.",
        "data": {
            "follow_count": follow_count,
        },
    }


@router.get(
    "/{userId}/followers",
    summary="List followers of a user",
)
async def get_followers(
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    page_start: int | None = Query(default=None, alias="pageStart"),
    page_size: int = Query(default=20, ge=1, le=200, alias="pageSize"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    """Return followers of the given user (cursor-based pagination)."""
    if page_size <= 0:
        page_size = 20

    user_repo = UserRepository(session=db)
    profile_repo = UserProfileRepository(session=db)
    follow_repo = UserFollowingRepository(session=db)
    stats_repo = UserStatisticsRepository(session=db)
    auth_service = UserAuthService(
        user_repo=user_repo,
        profile_repo=profile_repo,
        follow_repo=follow_repo,
        stats_repo=stats_repo,
    )

    target = await user_repo.get_by_id(user_id)
    if target is None:
        raise NotFoundError("User not found")

    all_follower_ids_stmt = (
        select(UserFollowingRelationship.follower_id)
        .where(
            UserFollowingRelationship.followee_id == user_id,
            UserFollowingRelationship.deleted_at.is_(None),
        )
        .order_by(UserFollowingRelationship.follower_id.asc())
    )
    all_result = await db.execute(all_follower_ids_stmt)
    all_follower_ids = [r[0] for r in all_result.all()]

    if page_start is not None:
        try:
            start_idx = all_follower_ids.index(page_start)
        except ValueError:
            start_idx = 0
    else:
        start_idx = 0

    end_idx = start_idx + page_size
    page_follower_ids = all_follower_ids[start_idx:end_idx]

    followers: list[dict] = []
    for fid in page_follower_ids:
        user = await user_repo.get_by_id(fid)
        profile = await profile_repo.get_profile_by_user_id(fid)
        if user is None or profile is None:
            continue
        followers.append(
            await auth_service.build_user_dto(
                user=user,
                profile=profile,
                viewer_id=auth_user.user_id if auth_user.user_id > 0 else None,
            )
        )

    returned = len(followers)
    has_prev = start_idx > 0
    prev_start = all_follower_ids[0] if has_prev and len(all_follower_ids) > 0 else 0
    has_more = end_idx < len(all_follower_ids)
    next_start = all_follower_ids[end_idx] if has_more else 0

    first_id = page_follower_ids[0] if page_follower_ids else 0
    page = {
        "pageStart": first_id,
        "pageSize": returned,
        "hasPrev": has_prev,
        "prevStart": prev_start,
        "hasMore": has_more,
        "nextStart": next_start,
    }

    return {
        "code": 200,
        "message": "Query followers successfully.",
        "data": {
            "users": followers,
            "page": page,
        },
    }


@router.get(
    "/{userId}/follow/users",
    summary="List followees of a user",
)
async def get_followees(
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    page_start: int | None = Query(default=None, alias="pageStart"),
    page_size: int = Query(default=20, ge=1, le=200, alias="pageSize"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    """Return users that the given user is following (cursor-based pagination)."""
    if page_size <= 0:
        page_size = 20

    user_repo = UserRepository(session=db)
    profile_repo = UserProfileRepository(session=db)
    follow_repo = UserFollowingRepository(session=db)
    stats_repo = UserStatisticsRepository(session=db)
    auth_service = UserAuthService(
        user_repo=user_repo,
        profile_repo=profile_repo,
        follow_repo=follow_repo,
        stats_repo=stats_repo,
    )

    target = await user_repo.get_by_id(user_id)
    if target is None:
        raise NotFoundError("User not found")

    all_followee_ids_stmt = (
        select(UserFollowingRelationship.followee_id)
        .where(
            UserFollowingRelationship.follower_id == user_id,
            UserFollowingRelationship.deleted_at.is_(None),
        )
        .order_by(UserFollowingRelationship.followee_id.asc())
    )
    all_result = await db.execute(all_followee_ids_stmt)
    all_followee_ids = [r[0] for r in all_result.all()]

    if page_start is not None:
        try:
            start_idx = all_followee_ids.index(page_start)
        except ValueError:
            start_idx = 0
    else:
        start_idx = 0

    end_idx = start_idx + page_size
    page_followee_ids = all_followee_ids[start_idx:end_idx]

    followees: list[dict] = []
    for fid in page_followee_ids:
        user = await user_repo.get_by_id(fid)
        profile = await profile_repo.get_profile_by_user_id(fid)
        if user is None or profile is None:
            continue
        followees.append(
            await auth_service.build_user_dto(
                user=user,
                profile=profile,
                viewer_id=auth_user.user_id if auth_user.user_id > 0 else None,
            )
        )

    returned = len(followees)
    has_prev = start_idx > 0
    prev_start = all_followee_ids[0] if has_prev and len(all_followee_ids) > 0 else 0
    has_more = end_idx < len(all_followee_ids)
    next_start = all_followee_ids[end_idx] if has_more else 0

    first_id = page_followee_ids[0] if page_followee_ids else 0
    page = {
        "pageStart": first_id,
        "pageSize": returned,
        "hasPrev": has_prev,
        "prevStart": prev_start,
        "hasMore": has_more,
        "nextStart": next_start,
    }

    return {
        "code": 200,
        "message": "Query followees successfully.",
        "data": {
            "users": followees,
            "page": page,
        },
    }


@router.get(
    "/{userId}/follow/questions",
    summary="List questions followed by user",
)
async def get_user_followed_questions(
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    page_start: int | None = Query(default=None, alias="pageStart"),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    question_repo = QuestionRepository(session=db)
    topic_repo = QuestionTopicRepository(session=db)

    offset = page_start or 0
    rows, total = await question_repo.list_followed(
        user_id=user_id, limit=page_size, offset=offset
    )
    topic_map = await topic_repo.list_topic_ids([row.id for row in rows])

    questions = []
    for row in rows:
        created_at_ms = int(row.created_at.timestamp() * 1000) if row.created_at else 0
        updated_at_ms = int(row.updated_at.timestamp() * 1000) if row.updated_at else 0
        dto = {
            "id": row.id,
            "title": row.title,
            "content": None,
            "type": row.type,
            "groupId": row.group_id,
            "bounty": row.bounty,
            "acceptedAnswerId": row.accepted_answer_id,
            "createdBy": row.created_by_id,
            "createdAt": created_at_ms,
            "updatedAt": updated_at_ms,
            "topicIds": topic_map.get(row.id, []),
        }
        questions.append(dto)

    returned = len(questions)
    has_more = offset + returned < total
    next_start = offset + returned if has_more and returned > 0 else None
    page = {
        "pageStart": offset,
        "pageSize": returned,
        "hasMore": has_more,
        "nextStart": next_start,
        "total": total,
    }
    return {
        "code": 200,
        "message": "Query followed questions successfully.",
        "data": {
            "questions": questions,
            "page": page,
        },
    }


@router.get(
    "/{userId}/questions",
    summary="List questions asked by user",
)
async def get_user_questions(
    user_id: Annotated[int, Path(alias="userId")],
    page_start: int | None = Query(default=None, alias="pageStart"),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    if user_id < 1:
        raise NotFoundError("User not found")
    user_repo = UserRepository(session=db)
    target = await user_repo.get_by_id(user_id)
    if target is None:
        raise NotFoundError("User not found")
    question_repo = QuestionRepository(session=db)
    topic_repo = QuestionTopicRepository(session=db)

    offset = page_start or 0
    rows, total = await question_repo.list_by_user(
        user_id=user_id, limit=page_size, offset=offset
    )
    topic_map = await topic_repo.list_topic_ids([row.id for row in rows])

    questions = []
    for row in rows:
        created_at_ms = int(row.created_at.timestamp() * 1000) if row.created_at else 0
        updated_at_ms = int(row.updated_at.timestamp() * 1000) if row.updated_at else 0
        dto = {
            "id": row.id,
            "title": row.title,
            "content": None,
            "type": row.type,
            "groupId": row.group_id,
            "bounty": row.bounty,
            "acceptedAnswerId": row.accepted_answer_id,
            "createdBy": row.created_by_id,
            "createdAt": created_at_ms,
            "updatedAt": updated_at_ms,
            "topicIds": topic_map.get(row.id, []),
        }
        questions.append(dto)

    returned = len(questions)
    has_more = offset + returned < total
    next_start = offset + returned if has_more and returned > 0 else None
    page = {
        "pageStart": offset,
        "pageSize": returned,
        "hasMore": has_more,
        "nextStart": next_start,
        "total": total,
    }
    return {
        "code": 200,
        "message": "Query asked questions successfully.",
        "data": {
            "questions": questions,
            "page": page,
        },
    }


@router.get(
    "/{userId}/answers",
    summary="List answers posted by user",
)
async def get_user_answers(
    user_id: Annotated[int, Path(alias="userId")],
    page_start: int | None = Query(default=None, alias="pageStart"),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    if user_id < 1:
        raise NotFoundError("User not found")
    user_repo = UserRepository(session=db)
    target = await user_repo.get_by_id(user_id)
    if target is None:
        raise NotFoundError("User not found")

    answer_repo = AnswerRepository(session=db)
    profile_repo = UserProfileRepository(session=db)

    all_ids = await answer_repo.list_all_answer_ids_by_user(user_id)

    if page_start is not None:
        try:
            start_idx = all_ids.index(page_start)
        except ValueError:
            start_idx = 0
    else:
        start_idx = 0

    end_idx = start_idx + page_size
    page_ids = all_ids[start_idx:end_idx]

    cursor = page_start if page_start else (all_ids[0] if all_ids else None)
    rows, total = await answer_repo.list_by_user(
        user_id=user_id, limit=page_size, cursor=cursor
    )

    profile = await profile_repo.get_profile_by_user_id(user_id)
    sender = None
    if profile:
        sender = {
            "id": profile.user_id,
            "nickname": profile.nickname,
            "avatarId": profile.avatar_id,
            "intro": profile.intro,
        }

    answers = []
    for row in rows:
        created_at_ms = int(row.created_at.timestamp() * 1000) if row.created_at else 0
        updated_at_ms = int(row.updated_at.timestamp() * 1000) if row.updated_at else 0
        dto = {
            "id": row.id,
            "questionId": row.question_id,
            "content": row.content,
            "createdBy": row.created_by_id,
            "sender": sender,
            "createdAt": created_at_ms,
            "updatedAt": updated_at_ms,
        }
        answers.append(dto)

    returned = len(answers)
    has_more = end_idx < len(all_ids)
    next_start = all_ids[end_idx] if has_more else 0

    first_id = page_ids[0] if page_ids else 0
    page = {
        "pageStart": first_id,
        "pageSize": returned,
        "hasMore": has_more,
        "nextStart": next_start,
        "total": total,
    }
    return {
        "code": 200,
        "message": "Query answered questions successfully.",
        "data": {
            "answers": answers,
            "page": page,
        },
    }


_EMAIL_FORMAT = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def _own_email(raw: str) -> str:
    """An address an account may hold: well formed, and one mail can reach."""
    email = raw.strip()
    if not _EMAIL_FORMAT.match(email) or is_placeholder_email(email):
        raise UnprocessableEntityError(
            "Invalid email address format", {"reason": "invalid_email"}
        )
    return email


def _email_taken() -> ConflictError:
    return ConflictError("Email already registered", {"reason": "email_taken"})


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


@router.post(
    "/verify/email",
    summary="Send registration email verification code",
)
async def send_register_email_code(
    payload: SendEmailCodeRequest,
    request: Request,
    db=Depends(get_db),
) -> dict:
    """Send email verification code for registration.

    Uses Redis for code storage (10 min TTL) and sends via configured SMTP.
    Answers success without sending if email is not configured (for dev); a
    configured sender that fails is a 503 and leaves no code behind.
    Any valid email address is accepted.
    """
    import re

    from redis.asyncio import Redis as AsyncRedis

    from app.core.client_address import resolved_client_address
    from app.core.config import settings
    from app.core.errors import ConflictError
    from app.domain.user.verification_service import EmailVerificationService

    email = payload.email
    invite_code = _normalize_registration_invite_code(
        payload.invite_code, required=settings.require_invite_code
    )

    if invite_code:
        from app.domain.invite.services import InviteCodeService

        try:
            await InviteCodeService(db).validate_code(invite_code)
        except ValueError as exc:
            raise _invite_code_error(exc) from exc

    email_regex = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    if not re.match(email_regex, email):
        raise UnprocessableEntityError("Invalid email address format")

    user_repo = UserRepository(session=db)
    if await user_repo.is_email_taken(email):
        raise ConflictError("Email already registered")

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        service = EmailVerificationService(redis)
        await service.send_verification_code(email, resolved_client_address(request))
    finally:
        await redis.aclose()

    return {
        "code": 201,
        "message": "Send email successfully.",
    }


@router.get(
    "/registration-config",
    summary="Get registration configuration",
    openapi_extra={"x-public": True},
)
async def get_registration_config() -> dict:
    """Return public registration settings so the frontend can adapt its UI."""
    from app.core.config import settings

    return {
        "code": 200,
        "message": "Success",
        "data": {
            "requireInviteCode": settings.require_invite_code,
        },
    }


@router.post(
    "",
    summary="Register User",
)
async def register_user(
    payload: RegisterUserRequest,
    request: Request,
    response: Response,
    auth_service: UserAuthService = Depends(get_user_auth_service),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Registration flow with email verification."""
    from redis.asyncio import Redis as AsyncRedis

    from app.core.client_address import resolved_client_address
    from app.domain.user.login_security import ClientFailureBudget
    from app.domain.user.verification_service import EmailVerificationService

    username = payload.username
    nickname = payload.nickname
    email = payload.email
    email_code = payload.email_code
    password = payload.password
    invite_code = _normalize_registration_invite_code(
        payload.invite_code, required=settings.require_invite_code
    )
    consent_problem = _signup_consent(
        payload.consent.documents if payload.consent else None,
        payload.consent.method if payload.consent else None,
    )
    if consent_problem:
        raise UnprocessableEntityError(consent_problem)
    assert payload.consent is not None

    if invite_code:
        from app.domain.invite.services import InviteCodeService

        invite_service = InviteCodeService(session)
        try:
            await invite_service.validate_code(invite_code)
        except ValueError as exc:
            raise _invite_code_error(exc) from exc

    if not username or not nickname or not email:
        raise BadRequestError("username, nickname, and email are required")
    if not email_code:
        raise BadRequestError("emailCode is required")

    if not is_valid_username(username):
        raise UnprocessableEntityError("Invalid username format")
    # Next to the format check, not down in the service (#345): the service
    # runs after email-code verification, so a reserved name would only be
    # refused once the user had already gone and fetched a code for a name they
    # were never allowed to have. The service keeps its own check as a backstop
    # for callers that don't come through here.
    if is_reserved_username(username):
        raise UnprocessableEntityError("该用户名是平台保留字，请换一个")

    nickname = normalize_nickname(nickname)

    _require_new_password(password)

    # Always verify email code, regardless of invite code
    client = resolved_client_address(request)
    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        client_budget = ClientFailureBudget(redis, "email_code")
        wait = await client_budget.spend(client)
        if wait:
            raise _too_many_from_client(wait)
        service = EmailVerificationService(redis)
        is_valid = await service.verify_code(email, email_code)
        if not is_valid:
            raise UnprocessableEntityError(
                "Invalid or expired verification code",
                {"reason": "invalid_email_code"},
            )
        await client_budget.refund(client)
    finally:
        await redis.aclose()

    try:
        user, profile = await auth_service.register_with_password(
            username=username,
            nickname=nickname,
            email=email,
            password=password,
        )
    except ValueError as exc:
        msg = str(exc)
        if msg == "USERNAME_TAKEN":
            raise ConflictError("Username already registered") from exc
        if msg == "USERNAME_RESERVED":
            # Deliberately says WHY rather than reusing "already registered":
            # nobody holds this name, and telling the user it is taken would be
            # a lie they cannot act on (#345).
            raise UnprocessableEntityError("该用户名是平台保留字，请换一个") from exc
        if msg == "EMAIL_TAKEN":
            raise ConflictError("Email already registered") from exc
        raise

    ip, user_agent = client_context(request)
    await ConsentService(session).record(
        user_id=user.id,
        accepted=payload.consent.documents,
        method=payload.consent.method,
        entry="signup",
        ip=ip,
        user_agent=user_agent,
    )

    # Consume invite code after successful registration
    if invite_code:
        from app.domain.invite.services import InviteCodeService

        invite_service = InviteCodeService(session)
        try:
            await invite_service.consume_code(invite_code)
        except ValueError as exc:
            raise _invite_code_error(exc) from exc

    access_token = await issue_session(
        response,
        request,
        session,
        user_id=user.id,
        handle=user.username,
        login_method="signup",
    )

    user_dto = await auth_service.build_user_dto(
        user=user,
        profile=profile,
        viewer_id=user.id,
    )
    # Dependency teardown commits after the response, and the client asks for
    # its pending consents the moment it holds the token: uncommitted, the
    # consent given here reads as missing and the rules are put to it again.
    await session.commit()
    return {
        "code": 201,
        "message": "Register successfully.",
        "data": {
            "user": user_dto,
            "accessToken": access_token,
        },
    }


@router.get(
    "/me",
    summary="Get current user",
)
async def get_current_user(
    auth_user: AuthUserInfo = Depends(require_auth_user),
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    """Return the profile of the current authenticated user."""
    try:
        user, profile = await auth_service.get_user_with_profile(auth_user.user_id)
    except ValueError:
        raise NotFoundError("User not found") from None

    user_dto = await auth_service.build_user_dto(
        user=user,
        profile=profile,
        viewer_id=auth_user.user_id,
    )
    return {
        "code": 200,
        "message": "Query current user successfully.",
        "data": {
            "user": user_dto,
        },
    }


# How recent the sign-in behind a request must be for it to add the account's
# first email. That address is what the account is recovered through, so a
# session taken from its owner must not be able to attach one of its own; and
# an account without an address usually has nothing else to confirm with.
ADD_EMAIL_SIGN_IN_WINDOW = timedelta(minutes=15)


async def _account_without_email(
    auth_user: AuthUserInfo,
    auth_service: UserAuthService,
    session: AsyncSession,
    session_id: uuid.UUID | None,
):
    """The caller's account, refused once it holds an address of its own
    (replacing one is a different operation, with its own confirmation), or
    when the caller did not sign in within ``ADD_EMAIL_SIGN_IN_WINDOW``.
    Refreshing does not count as signing in: it keeps the session's start."""
    try:
        user, profile = await auth_service.get_user_with_profile(auth_user.user_id)
    except ValueError:
        raise NotFoundError("User not found") from None
    if not is_placeholder_email(user.email):
        raise ConflictError(
            "This account already has an email address", {"reason": "email_present"}
        )
    started = None
    if session_id is not None:
        started = await session.scalar(
            select(UserSession.created_at).where(
                UserSession.id == session_id,
                UserSession.user_id == user.id,
                UserSession.revoked_at.is_(None),
            )
        )
    if started is None or datetime.now(UTC) - started > ADD_EMAIL_SIGN_IN_WINDOW:
        raise ForbiddenError(
            "Sign in again to add an email address", {"reason": "reauth_required"}
        )
    return user, profile


@router.post(
    "/me/email/code",
    summary="Send a code to the address an account without one is adding",
)
async def send_add_email_code(
    payload: AddEmailCodeRequest,
    request: Request,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    auth_service: UserAuthService = Depends(get_user_auth_service),
    session: AsyncSession = Depends(get_db),
    session_id: uuid.UUID | None = Depends(get_current_session_id),
) -> dict:
    await _account_without_email(auth_user, auth_service, session, session_id)
    email = _own_email(payload.email)
    if await auth_service.get_user_by_email(email) is not None:
        raise _email_taken()
    await _send_email_code(request, email)
    return {"code": 200, "message": "Verification code sent."}


@router.post(
    "/me/email",
    summary="Add a verified email to an account that has none",
)
async def add_email(
    payload: AddEmailRequest,
    request: Request,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    auth_service: UserAuthService = Depends(get_user_auth_service),
    session: AsyncSession = Depends(get_db),
    session_id: uuid.UUID | None = Depends(get_current_session_id),
) -> dict:
    from sqlalchemy.exc import IntegrityError

    user, profile = await _account_without_email(
        auth_user, auth_service, session, session_id
    )
    email = _own_email(payload.email)
    # Before the code is spent: an address that cannot be taken should not
    # cost the person their code.
    if await auth_service.get_user_by_email(email) is not None:
        raise _email_taken()
    await _check_email_code(request, email, payload.code)
    try:
        await UserRepository(session=session).update_email(user, email)
    except IntegrityError:
        raise _email_taken() from None
    user_dto = await auth_service.build_user_dto(
        user=user, profile=profile, viewer_id=user.id
    )
    return {
        "code": 200,
        "message": "Email added.",
        "data": {"user": user_dto},
    }


@router.get(
    "/me/auth-methods",
    summary="How the signed-in user can confirm their identity",
)
async def get_my_auth_methods(
    auth_user: AuthUserInfo = Depends(require_auth_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """The ways ``/auth/sudo`` accepts from this account, for the caller only:
    what an account has is nobody else's business."""
    from app.domain.passkey.models import PasskeyCredential
    from app.domain.user.login_security import TOTPService
    from app.domain.user.models import User

    user = await session.get(User, auth_user.user_id)
    if user is None:
        raise NotFoundError("User not found")
    passkeys = await session.scalar(
        select(func.count())
        .select_from(PasskeyCredential)
        .where(PasskeyCredential.user_id == user.id)
    )
    two_factor = await TOTPService(session).is_2fa_enabled(user.id)
    return {
        "code": 200,
        "message": "Success",
        "data": {
            # An account created through a third-party sign-in may have none.
            "password": bool(user.hashed_password),
            "passkey": bool(passkeys),
            "twoFactor": two_factor,
            "emailCode": _email_code_confirms(user.email, two_factor=two_factor),
        },
    }


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


@router.post(
    "/me/sudo/email-code",
    summary="Mail a code that confirms the signed-in user's identity",
)
async def request_sudo_email_code(
    request: Request,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Sent to the account's own address, under the same quotas as every
    other mailed code. Refused where ``/auth/sudo`` would refuse the code."""
    from redis.asyncio import Redis as AsyncRedis

    from app.core.client_address import resolved_client_address
    from app.domain.user.login_security import TOTPService
    from app.domain.user.models import User
    from app.domain.user.verification_service import (
        EmailCodePurpose,
        EmailVerificationService,
    )

    user = await session.get(User, auth_user.user_id)
    if user is None:
        raise NotFoundError("User not found")
    two_factor = await TOTPService(session).is_2fa_enabled(user.id)
    if not _email_code_confirms(user.email, two_factor=two_factor):
        raise _email_code_unavailable()

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        await EmailVerificationService(
            redis, EmailCodePurpose.SUDO
        ).send_verification_code(user.email, resolved_client_address(request))
    finally:
        await redis.aclose()
    return {"code": 200, "message": "Code sent.", "data": {"email": user.email}}


@router.get(
    "/{userId}",
    summary="Get user by id",
)
async def get_user(
    user_id: Annotated[int, Path(alias="userId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    """Return the public profile of a user."""
    if user_id < 1:
        raise NotFoundError("User not found")
    try:
        user, profile = await auth_service.get_user_with_profile(user_id)
    except ValueError:
        raise NotFoundError("User not found") from None

    user_dto = await auth_service.build_user_dto(
        user=user,
        profile=profile,
        viewer_id=auth_user.user_id if auth_user.user_id > 0 else None,
    )
    return {
        "code": 200,
        "message": "Query user successfully.",
        "data": {
            "user": user_dto,
        },
    }


@router.patch(
    "/{userId}",
    summary="Update user profile (partial)",
)
async def patch_user_profile(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    profile_service: UserProfileService = Depends(get_user_profile_service),
) -> dict:
    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can update their profile.")
    await profile_service.update_profile(
        user_id=user_id,
        nickname=payload.get("nickname"),
        intro=payload.get("intro"),
        avatar_id=payload.get("avatarId"),
    )
    return {"code": 200, "message": "Success", "data": {}}


@router.put(
    "/{userId}",
    summary="Update user profile (full)",
)
async def put_user_profile(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    profile_service: UserProfileService = Depends(get_user_profile_service),
) -> dict:
    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can update their profile.")
    await profile_service.update_profile(
        user_id=user_id,
        nickname=payload.get("nickname"),
        intro=payload.get("intro"),
        avatar_id=payload.get("avatarId"),
    )
    return {"code": 200, "message": "Success", "data": {}}


async def _admit_login_attempt(delay: "LoginDelay", username: str) -> int:
    """Admit one password check for ``username``, or refuse it while the wait
    earlier failures started is still running. Returns the wait this attempt
    starts if the password turns out wrong. The caller clears the count once
    the password holds."""
    admission = await delay.admit(username)
    if not admission.admitted:
        raise ForbiddenError(
            f"Too many failed attempts. Try again in {admission.wait_seconds} seconds",
            {
                "reason": "too_many_attempts",
                "retryAfterSeconds": admission.wait_seconds,
            },
        )
    return admission.wait_seconds


def _too_many_from_client(wait_seconds: int) -> ForbiddenError:
    return ForbiddenError(
        "Too many failed attempts from this network. "
        f"Try again in {wait_seconds} seconds",
        {"reason": "too_many_attempts", "retryAfterSeconds": wait_seconds},
    )


def _wrong_password(wait_seconds: int) -> AuthenticationRequiredError:
    if wait_seconds == 0:
        return AuthenticationRequiredError(
            "Invalid username or password", {"reason": "invalid_credentials"}
        )
    return AuthenticationRequiredError(
        f"Invalid username or password. Try again in {wait_seconds} seconds",
        {"reason": "invalid_credentials", "retryAfterSeconds": wait_seconds},
    )


@router.post(
    "/auth/login",
    summary="User Login",
)
async def user_login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    auth_service: UserAuthService = Depends(get_user_auth_service),
    session: AsyncSession = Depends(get_db),
) -> dict:
    from redis.asyncio import Redis as AsyncRedis

    from app.core.client_address import resolved_client_address
    from app.core.config import settings
    from app.domain.user.login_security import (
        ClientFailureBudget,
        LoginDelay,
        TOTPService,
    )

    username = payload.username
    password = payload.password
    totp_code = payload.totp_code
    client = resolved_client_address(request)

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        login_delay = LoginDelay(redis)
        client_budget = ClientFailureBudget(redis, "login")

        wait = await client_budget.spend(client)
        if wait:
            raise _too_many_from_client(wait)
        wrong = False
        try:
            wait_if_wrong = await _admit_login_attempt(login_delay, username)
            auth_result = await auth_service.authenticate(
                username=username, password=password
            )
            wrong = auth_result is None
        finally:
            # Only a wrong password counts against the address; an attempt
            # refused or broken off before the check proved nothing.
            if not wrong:
                await client_budget.refund(client)
        if auth_result is None:
            raise _wrong_password(wait_if_wrong)

        user, profile = auth_result
        # Cleared as soon as the password is right, not after 2FA: the attempt
        # was counted up front, so returning "2FA required" first would leave
        # it counted. The second step has a budget of its own.
        await login_delay.clear(username)

        totp_service = TOTPService(session)
        requires_2fa = await totp_service.is_2fa_enabled(user.id)
        trust = (
            await _trusted_device(request, session, user.id) if requires_2fa else None
        )

        if requires_2fa and trust is None:
            if not totp_code:
                # tempToken lets the client finish via POST /auth/verify-2fa.
                return {
                    "code": 200,
                    "message": "2FA required",
                    "data": {
                        "requires2FA": True,
                        "userId": user.id,
                        "tempToken": await _issue_2fa_pending_token(user.id),
                    },
                }
            # This inline branch checks a TOTP code without ever minting a
            # ticket, so the single-use ticket does nothing for it — without
            # its own budget it stays exactly the oracle #357 describes.
            await _spend_2fa_attempt(
                redis,
                user.id,
                lambda: totp_service.verify_2fa(user.id, totp_code),
                client=client,
            )

        access_token = await issue_session(
            response,
            request,
            session,
            user_id=user.id,
            handle=user.username,
            login_method="totp" if requires_2fa and trust is None else "password",
            trust=trust,
        )

        user_dto = await auth_service.build_user_dto(
            user=user,
            profile=profile,
            viewer_id=user.id,
        )
        return {
            "code": 201,
            "message": "Login successfully.",
            "data": {
                "user": user_dto,
                "accessToken": access_token,
                "requires2FA": False,
                "passkeyEnrollment": await _passkey_enrollment(user.id, session),
            },
        }
    finally:
        await redis.aclose()


@router.post(
    "/auth/verify-2fa",
    summary="Complete a 2FA-gated login",
)
async def verify_2fa_login(
    payload: dict,
    request: Request,
    response: Response,
    auth_service: UserAuthService = Depends(get_user_auth_service),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Second step of a 2FA login: exchange the short-lived ``2fa_pending``
    token from the password step plus a TOTP code (or a one-time backup
    code) for real session tokens. Reference contract: POST {temp_token, code}.
    With ``trust_device`` true, this browser is trusted to skip the step for
    30 days.

    Two independent bounds keep this from being a code oracle for anyone
    holding a leaked password (#357): the ticket is redeemable exactly once
    whether the code was right or wrong, and the user has a per-15-minutes
    attempt budget that a successful password step does *not* reset.
    """
    from redis.asyncio import Redis as AsyncRedis

    from app.common.auth import verify_2fa_pending_token
    from app.core.client_address import resolved_client_address
    from app.core.config import settings
    from app.core.single_use_state import SingleUseUnavailableError, claim
    from app.domain.user.login_security import TOTPService

    temp_token = payload.get("temp_token") or ""
    code = (payload.get("code") or "").strip()
    trust_device = payload.get("trust_device") is True
    if not temp_token or not code:
        raise BadRequestError("temp_token and code are required")

    claims = verify_2fa_pending_token(temp_token)
    if claims is None:
        raise AuthenticationRequiredError(
            "Invalid or expired 2FA session token", {"reason": "session_expired"}
        )
    user_id = claims.user_id

    # Burn the ticket before the code is checked, so a wrong guess costs one
    # too. DELETE is atomic, which is what makes this hold against a burst of
    # simultaneous redemptions of the same ticket rather than just against a
    # sequential replay — and that fan-out bound is the whole job of the
    # ticket. A wrong code gets a *replacement* ticket below, on the original
    # deadline, so bounding fan-out costs an honest user nothing.
    try:
        first_use = await claim(_PENDING_2FA_SCOPE, claims.jti)
    except SingleUseUnavailableError:
        logger.exception("2fa: cannot claim pending ticket uid=%s", user_id)
        raise InternalServerError("暂时无法完成两步验证，请稍后重试") from None
    if not first_use:
        raise AuthenticationRequiredError(
            "Invalid or expired 2FA session token", {"reason": "session_expired"}
        )

    # A backup code is 8 hex chars and a TOTP is 6 digits, so the shape says
    # which one the user meant — and each gets its own budget. Falling back
    # from one to the other (the old behaviour) would make "which credential
    # was this attempt against?" unanswerable, and there is no input that
    # both accept.
    is_backup_code = not (len(code) == 6 and code.isdigit())

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        totp_service = TOTPService(session)
        await _spend_2fa_attempt(
            redis,
            user_id,
            (
                (lambda: totp_service.verify_backup_code(user_id, code))
                if is_backup_code
                else (lambda: totp_service.verify_2fa(user_id, code))
            ),
            is_backup_code=is_backup_code,
            reissue_until=claims.expires_at,
            client=resolved_client_address(request),
        )
        used_backup_code = is_backup_code

        try:
            user, profile = await auth_service.get_user_with_profile(user_id)
        except ValueError as exc:
            raise AuthenticationRequiredError(str(exc)) from exc

        access_token = await issue_session(
            response,
            request,
            session,
            user_id=user.id,
            handle=user.username,
            login_method="backup_code" if used_backup_code else "totp",
            grant_trust=trust_device,
        )

        user_dto = await auth_service.build_user_dto(
            user=user,
            profile=profile,
            viewer_id=user.id,
        )
        return {
            "code": 201,
            "message": (
                "Login successfully. Note: This backup code has expired. "
                "Please generate a new backup code for future use."
                if used_backup_code
                else "Login successfully."
            ),
            "data": {
                "user": user_dto,
                "accessToken": access_token,
                "requires2FA": False,
                "usedBackupCode": used_backup_code,
                "passkeyEnrollment": await _passkey_enrollment(user.id, session),
            },
        }
    finally:
        await redis.aclose()


_EMAIL_FORMAT = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")

# The sign-in code request answers with this one body whatever the address:
# known, unknown or a placeholder. Anything that differed would tell the
# caller whether an account uses that address.
_SIGN_IN_CODE_REQUESTED = {
    "code": 200,
    "message": "If an account uses this email, a sign-in code has been sent.",
}


def _invalid_email_code() -> AuthenticationRequiredError:
    return AuthenticationRequiredError(
        "Invalid or expired code", {"reason": "invalid_email_code"}
    )


async def _mail_sign_in_code(email: str) -> None:
    """Issue a sign-in code and mail it; runs after the response has gone,
    so that the response takes as long for an unknown address as for a known
    one. A failed send is only logged, for the same reason."""
    from redis.asyncio import Redis as AsyncRedis

    from app.domain.user.verification_service import (
        EmailCodePurpose,
        EmailVerificationService,
        Issued,
    )

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        service = EmailVerificationService(redis, EmailCodePurpose.SIGN_IN)
        issued = await service.issue(email)
        if issued is not Issued.SENT:
            logger.warning("Sign-in code mail was not sent: %s", issued.value)
    finally:
        await redis.aclose()


@router.post(
    "/auth/email-code",
    summary="Mail a sign-in code",
)
async def request_sign_in_code(
    payload: EmailCodeSignInRequest,
    request: Request,
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    """Mail a single-use sign-in code to the account using this address.

    The quota is spent before the account is looked up, so an unknown address
    is refused exactly when a known one would be, and otherwise gets the same
    answer while nothing is sent.
    """
    from redis.asyncio import Redis as AsyncRedis

    from app.core.background import spawn
    from app.core.client_address import resolved_client_address
    from app.domain.user.verification_service import (
        EmailCodePurpose,
        EmailVerificationService,
    )

    email = payload.email.strip()
    if not _EMAIL_FORMAT.match(email):
        raise UnprocessableEntityError("Invalid email address format")

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        await EmailVerificationService(redis, EmailCodePurpose.SIGN_IN).claim(
            email, resolved_client_address(request)
        )
    finally:
        await redis.aclose()

    user = await auth_service.get_user_by_email(email)
    if user is not None and not is_placeholder_email(user.email):
        spawn(_mail_sign_in_code(user.email), name="sign-in code mail")
    return _SIGN_IN_CODE_REQUESTED


@router.post(
    "/auth/email-code/verify",
    summary="Sign in with a mailed code",
)
async def verify_sign_in_code(
    payload: EmailCodeSignInVerifyRequest,
    request: Request,
    response: Response,
    auth_service: UserAuthService = Depends(get_user_auth_service),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """The mailed code is a first step like a password: an account with 2FA
    gets the same ``2fa_pending`` ticket the password step hands out, unless
    this browser is trusted to skip it."""
    from redis.asyncio import Redis as AsyncRedis

    from app.core.client_address import resolved_client_address
    from app.domain.user.login_security import ClientFailureBudget, TOTPService
    from app.domain.user.verification_service import (
        EmailCodePurpose,
        EmailVerificationService,
    )

    email = payload.email.strip()
    client = resolved_client_address(request)
    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        # Shared with the sign-up code: one source guessing mailed codes is
        # the same attack whichever form it types them into.
        client_budget = ClientFailureBudget(redis, "email_code")
        wait = await client_budget.spend(client)
        if wait:
            raise _too_many_from_client(wait)
        wrong = False
        try:
            service = EmailVerificationService(redis, EmailCodePurpose.SIGN_IN)
            wrong = not await service.verify_code(email, payload.code.strip())
        finally:
            if not wrong:
                await client_budget.refund(client)
    finally:
        await redis.aclose()
    if wrong:
        raise _invalid_email_code()

    user = await auth_service.get_user_by_email(email)
    if user is None or is_placeholder_email(user.email):
        raise _invalid_email_code()

    trust = None
    if await TOTPService(session).is_2fa_enabled(user.id):
        trust = await _trusted_device(request, session, user.id)
        if trust is None:
            return {
                "code": 200,
                "message": "2FA required",
                "data": {
                    "requires2FA": True,
                    "userId": user.id,
                    "tempToken": await _issue_2fa_pending_token(user.id),
                },
            }

    try:
        user, profile = await auth_service.get_user_with_profile(user.id)
    except ValueError as exc:
        raise _invalid_email_code() from exc

    access_token = await issue_session(
        response,
        request,
        session,
        user_id=user.id,
        handle=user.username,
        login_method="email_code",
        trust=trust,
    )
    user_dto = await auth_service.build_user_dto(
        user=user,
        profile=profile,
        viewer_id=user.id,
    )
    return {
        "code": 201,
        "message": "Login successfully.",
        "data": {
            "user": user_dto,
            "accessToken": access_token,
            "requires2FA": False,
            "passkeyEnrollment": await _passkey_enrollment(user.id, session),
        },
    }


@router.post(
    "/auth/refresh-token",
    summary="Refresh Access Token",
)
async def refresh_access_token(
    request: Request,
    response: Response,
    auth_service: UserAuthService = Depends(get_user_auth_service),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Trade the refresh cookie for a new access token, rotating the cookie.

    A 401 from here is the one answer that means the sign-in is over. A
    refresh racing another one with the same cookie gets an access token and
    no new cookie: the winner's response already carries the successor.
    """
    _require_same_origin(request)
    refresh_token = request.cookies.get(REFRESH_COOKIE)
    if not refresh_token:
        raise AuthenticationRequiredError("Refresh token is missing")

    refreshed = await SessionService(session).refresh(refresh_token)
    if refreshed is None:
        raise AuthenticationRequiredError("This sign-in has ended")

    try:
        user, profile = await auth_service.get_user_with_profile(refreshed.user_id)
    except ValueError as exc:
        raise AuthenticationRequiredError(str(exc)) from exc

    if refreshed.refresh_token is not None:
        _set_refresh_cookie(response, refreshed.refresh_token, refreshed.expires_at)
    access_token = create_access_token(
        user.id, handle=user.username, sid=refreshed.session_id
    )

    user_dto = await auth_service.build_user_dto(
        user=user,
        profile=profile,
        viewer_id=user.id,
    )
    return {
        "code": 201,
        "message": "Refresh token successfully.",
        "data": {
            "accessToken": access_token,
            "user": user_dto,
        },
    }


@router.post(
    "/auth/logout",
    summary="Logout",
)
async def user_logout(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db),
) -> dict:
    """End the session behind the refresh cookie and clear the cookie.

    Idempotent: with the cookie missing or its session already over, the
    client still gets the clearing header and a success response.
    """
    _require_same_origin(request)
    refresh_token = request.cookies.get(REFRESH_COOKIE)
    if refresh_token:
        await SessionService(session).end(refresh_token)
    _clear_refresh_cookie(response)
    return {
        "code": 201,
        "message": "Logout successfully.",
    }


@router.post(
    "/auth/sudo",
    summary="Verify credentials for privileged operations",
)
async def sudo_auth(
    payload: SudoAuthRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    current: uuid.UUID | None = Depends(get_current_session_id),
    auth_service: UserAuthService = Depends(get_user_auth_service),
    passkey_service: PasskeyService = Depends(get_passkey_service),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Re-prove who is at the keyboard, and hand back a ticket saying so.

    The ticket is the whole point (#389). Before it, this endpoint answered
    ``{"verified": true}`` and kept no record — nothing the server could check
    afterwards — so the gate it appeared to be existed only in the client, and
    the operation behind it took a session cookie and nothing more.

    A verification opens a sudo window of ten minutes on the caller's
    session, as does a sign-in whose credential this endpoint would have
    accepted. Inside it, a request with no ``method`` gets a ticket without
    proving anything again; outside it, that request is refused with
    ``SudoRequiredError``. Tickets stay single-use and bound to one purpose
    either way, and the window belongs to the session: revoking it ends the
    window, and the account's other sessions keep their own.
    """
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.domain.user.login_security import TOTPService
    from app.domain.user.verification_service import (
        EmailCodePurpose,
        EmailVerificationService,
    )

    method = payload.method
    credentials = payload.credentials
    sessions = SessionService(session)

    async def ticketed(message: str, **extra: Any) -> dict:
        data: dict[str, Any] = {"verified": True, **extra}
        if payload.purpose is not None:
            data["sudoTicket"] = await _issue_sudo_ticket(
                auth_user.user_id, payload.purpose
            )
        return {"code": 200, "message": message, "data": data}

    async def verified(message: str, **extra: Any) -> dict:
        if current is not None:
            await sessions.open_sudo(auth_user.user_id, current)
        return await ticketed(message, **extra)

    if method is None:
        if payload.purpose is None:
            raise BadRequestError("purpose is required")
        if current is None or not await sessions.in_sudo(auth_user.user_id, current):
            raise SudoRequiredError("Re-authentication required for this operation")
        return await ticketed("Sudo mode is active.")

    if method == "password":
        password = credentials.get("password")
        if not password:
            raise BadRequestError("password is required")

        user, _profile = await auth_service.get_user_with_profile(auth_user.user_id)
        if not user.hashed_password:
            raise AuthenticationRequiredError(
                "Password authentication not available for this account"
            )

        redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
        try:
            await _spend_sudo_password_attempt(
                redis,
                auth_user.user_id,
                lambda: auth_service.verify_password(user, password),
                message="Invalid password",
            )
        finally:
            await redis.aclose()

        return await verified("Sudo mode activated.")

    elif method == "totp":
        code = credentials.get("code")
        if not code:
            raise BadRequestError("code is required")

        redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
        try:
            totp_service = TOTPService(session)
            if not await totp_service.is_2fa_enabled(auth_user.user_id):
                raise AuthenticationRequiredError("2FA is not enabled")
            # Only TOTP is a step-up credential here: a backup code is never
            # checked at this entrance, so an 8-hex code simply fails as a
            # wrong TOTP and costs a step-up attempt like any other guess.
            await _spend_2fa_attempt(
                redis,
                auth_user.user_id,
                lambda: totp_service.verify_2fa(auth_user.user_id, code),
                step_up=True,
            )
        finally:
            await redis.aclose()

        return await verified("Sudo mode activated via 2FA.")

    elif method == "passkey":
        credential = credentials.get("passkeyResponse")
        if not isinstance(credential, dict):
            raise BadRequestError("passkeyResponse is required")
        challenge = _challenge_from_credential(credential)

        redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
        try:
            challenge_key = f"passkey:auth_challenge:{challenge}"
            if not await redis.get(challenge_key):
                raise BadRequestError("Invalid or expired challenge")
            await redis.delete(challenge_key)
        finally:
            await redis.aclose()

        # No attempt budget: a WebAuthn assertion is a signature rather than a
        # secret to guess, and the challenge above already makes it single-use.
        verified_user_id = await passkey_service.verify_authentication(
            challenge=challenge,
            credential=credential,
        )
        # A passkey proves an identity; this endpoint has to prove *this*
        # session's identity. Somebody else's key, presented from a stolen
        # session, would otherwise re-authenticate it.
        if verified_user_id != auth_user.user_id:
            raise AuthenticationRequiredError("Passkey does not belong to this account")

        return await verified("Sudo mode activated via passkey.")

    elif method == "email_code":
        code = credentials.get("code")
        if not code:
            raise BadRequestError("code is required")

        user, _profile = await auth_service.get_user_with_profile(auth_user.user_id)
        # Checked again here rather than trusted from when the code was sent:
        # 2FA switched on since then must still shut the mailbox out.
        two_factor = await TOTPService(session).is_2fa_enabled(user.id)
        if not _email_code_confirms(user.email, two_factor=two_factor):
            raise _email_code_unavailable()

        # No attempt budget of its own: each code dies after a few wrong
        # guesses, and a new one costs a mail from the address's quota.
        redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
        try:
            service = EmailVerificationService(redis, EmailCodePurpose.SUDO)
            matched = await service.verify_code(user.email, str(code).strip())
        finally:
            await redis.aclose()
        if not matched:
            raise _invalid_email_code()

        return await verified("Sudo mode activated via email code.")

    else:
        raise BadRequestError(f"Unknown auth method: {method}")


@router.get(
    "/{userId}/identity",
    summary="Get User Real Name Identity Info",
)
async def get_user_identity(
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    request: Request,
    precise: bool = Query(default=False),
    moduleType: str | None = Query(default=None),
    moduleEntityId: int | None = Query(default=None),
    accessReason: str | None = Query(default=None),
    accessType: str = Query(default="VIEW"),
    sudo_ticket: str | None = Query(default=None, alias="sudoTicket"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    realname_service: UserRealNameService = Depends(get_user_realname_service),
) -> dict:
    """The owner's identity, masked unless ``precise`` is asked for.

    Nobody else reads it in either form: the masked one still carries grade,
    major and class in full, which with a surname names a student. The check
    comes before the lookup, so a refusal says nothing about whether a record
    exists.

    The unmasked name and student ID take a fresh re-authentication: a
    session alone, stolen or left open, only ever reads the masked form.
    """
    from app.core.client_address import resolved_client_address

    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can view identity.")
    if precise:
        await _spend_sudo_ticket(
            sudo_ticket, user_id=auth_user.user_id, purpose=SudoPurpose.REALNAME_VIEW
        )

    try:
        if precise:
            identity = await realname_service.get_user_identity(user_id)
        else:
            identity = await realname_service.get_fuzzy_user_identity(user_id)

        data = {
            "hasIdentity": True,
            "identity": identity,
        }

        if precise:
            await realname_service.log_access(
                accessor_id=auth_user.user_id,
                target_id=user_id,
                access_reason=accessReason or "Precise real-name view",
                access_type=accessType,
                ip_address=resolved_client_address(request) or "",
                module_type=moduleType,
                module_entity_id=moduleEntityId,
            )
    except NotFoundError:
        data = {
            "hasIdentity": False,
            "identity": None,
        }

    return {"code": 200, "message": "Success", "data": data}


@router.put(
    "/{userId}/identity",
    summary="Update User Real Name Identity Info",
)
async def put_user_identity(
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    payload: PutUserIdentityRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    realname_service: UserRealNameService = Depends(get_user_realname_service),
) -> dict:
    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can update identity.")
    await _spend_sudo_ticket(
        payload.sudo_ticket,
        user_id=auth_user.user_id,
        purpose=SudoPurpose.REALNAME_UPDATE,
    )
    stored = await realname_service.create_or_update_user_identity(
        user_id=user_id,
        real_name=payload.real_name,
        student_id=payload.student_id,
        grade=payload.grade,
        major=payload.major,
        class_name=payload.class_name,
    )
    return {"code": 200, "message": "Success", "data": {"identity": stored}}


@router.patch(
    "/{userId}/identity",
    summary="Update User Real Name Identity Info (partial)",
)
async def patch_user_identity(
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    payload: PatchUserIdentityRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    realname_service: UserRealNameService = Depends(get_user_realname_service),
) -> dict:
    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can update identity.")
    await _spend_sudo_ticket(
        payload.sudo_ticket,
        user_id=auth_user.user_id,
        purpose=SudoPurpose.REALNAME_UPDATE,
    )
    try:
        existing = await realname_service.get_user_identity(user_id)
        base = existing.copy()
    except NotFoundError:
        base = {
            "realName": "",
            "studentId": "",
            "grade": "",
            "major": "",
            "className": "",
        }

    merged = {
        "realName": payload.real_name
        if payload.real_name is not None
        else base["realName"],
        "studentId": payload.student_id
        if payload.student_id is not None
        else base["studentId"],
        "grade": payload.grade if payload.grade is not None else base["grade"],
        "major": payload.major if payload.major is not None else base["major"],
        "className": payload.class_name
        if payload.class_name is not None
        else base["className"],
    }
    stored = await realname_service.create_or_update_user_identity(
        user_id=user_id,
        real_name=merged["realName"],
        student_id=merged["studentId"],
        grade=merged["grade"],
        major=merged["major"],
        class_name=merged["className"],
    )
    return {"code": 200, "message": "Success", "data": {"identity": stored}}


@router.delete(
    "/{userId}/identity",
    summary="Delete User Real Name Identity Info",
)
async def delete_user_identity(
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    payload: SudoTicketRequest = Body(default_factory=SudoTicketRequest),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    realname_service: UserRealNameService = Depends(get_user_realname_service),
) -> dict:
    """The owner removes their record. Who read it before stays on record."""
    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can delete identity.")
    await _spend_sudo_ticket(
        payload.sudo_ticket,
        user_id=auth_user.user_id,
        purpose=SudoPurpose.REALNAME_DELETE,
    )
    await realname_service.delete_user_identity(user_id)
    return {"code": 200, "message": "Success"}


@router.get(
    "/{userId}/identity/access-logs",
    summary="Get User Real Name Identity Access Logs",
)
async def get_user_identity_access_logs(
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    pageStart: int | None = Query(default=None),
    pageSize: int = Query(default=20, ge=1, le=200),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    realname_service: UserRealNameService = Depends(get_user_realname_service),
) -> dict:
    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can view identity access logs.")
    logs, page = await realname_service.get_access_logs(
        target_user_id=user_id,
        page_size=pageSize,
        page_start=pageStart,
    )
    data = {
        "logs": logs,
        "page": page,
    }
    return {"code": 200, "message": "Success", "data": data}


def _session_dto(
    row: UserSession, current: uuid.UUID | None, trusted: set[uuid.UUID]
) -> dict:
    return {
        "id": str(row.id),
        "loginMethod": row.login_method,
        "ipAddress": row.ip,
        "userAgent": row.user_agent,
        "createdAt": row.created_at.isoformat(),
        "lastActiveAt": row.last_used_at.isoformat(),
        "current": row.id == current,
        # Its browser is trusted to skip two-step verification.
        "trusted": row.id in trusted,
    }


@router.get(
    "/me/sessions",
    summary="List active sessions",
)
async def list_sessions(
    auth_user: AuthUserInfo = Depends(require_auth_user),
    current: uuid.UUID | None = Depends(get_current_session_id),
    session: AsyncSession = Depends(get_db),
) -> dict:
    rows = await SessionService(session).live(auth_user.user_id)
    trusted = await TrustedDeviceService(session).trusted_sessions(auth_user.user_id)
    return {
        "code": 200,
        "message": "OK",
        "data": {"sessions": [_session_dto(row, current, trusted) for row in rows]},
    }


@router.delete(
    "/me/sessions/{sessionId}",
    summary="Revoke a session",
)
async def revoke_session(
    session_id: Annotated[uuid.UUID, Path(alias="sessionId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """End one sign-in. Its refresh token stops working at once; an access
    token it already holds lasts out its few remaining minutes. Its browser
    is no longer trusted to skip two-step verification."""
    if not await SessionService(session).revoke(auth_user.user_id, session_id):
        raise NotFoundError("Session not found")
    await TrustedDeviceService(session).revoke_for_session(
        auth_user.user_id, session_id
    )
    return {
        "code": 200,
        "message": "Session revoked successfully.",
    }


@router.delete(
    "/me/sessions",
    summary="Revoke all other sessions",
)
async def revoke_all_sessions(
    auth_user: AuthUserInfo = Depends(require_auth_user),
    current: uuid.UUID | None = Depends(get_current_session_id),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Sign out every device but the one asking, and stop trusting every
    browser but its own."""
    count = await SessionService(session).revoke_all(
        auth_user.user_id, RevokeReason.REVOKED, keep=current
    )
    await TrustedDeviceService(session).revoke_all(
        auth_user.user_id, keep_session=current
    )
    return {
        "code": 200,
        "message": f"Revoked {count} sessions.",
        "data": {
            "revokedCount": count,
        },
    }


# Every exit of the recovery request answers with this one body: unknown
# address, rate-limited address and mailed address alike. Anything that differed
# between them would tell the caller whether an account uses that address.
_RECOVERY_REQUESTED = {
    "code": 200,
    "message": "If the email exists, a reset link has been sent.",
}


async def _send_recovery_mail(user_id: int, email: str, username: str) -> None:
    """Issue a reset token and mail it; runs after the response has gone.

    Off the request path so that the response takes as long for an unknown
    address as for a known one. A failed send, or one the site's hourly
    allowance refuses, is only logged: the requester was already told the same
    thing either way, and can ask again after the cooldown.
    """
    from redis.asyncio import Redis as AsyncRedis

    from app.core.email import get_email_sender
    from app.domain.user.login_security import PasswordResetService
    from app.domain.user.mail_quota import give_back_site_mail, take_site_mail

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        if not await take_site_mail(redis):
            logger.warning("Password recovery mail to user %d: site limit", user_id)
            return
        token = await PasswordResetService(redis).create_reset_token(
            user_id, email, username
        )
    finally:
        await redis.aclose()

    reset_url = f"{settings.frontend_url}/account/recover/password/verify?token={token}"
    subject = "[Cheese] Password Reset Request"
    body_html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <h2 style="color: #333;">Password Reset</h2>
        <p>You requested to reset your password. Click the link below:</p>
        <p><a href="{reset_url}" style="color: #007bff;">{reset_url}</a></p>
        <p>This link will expire in 30 minutes.</p>
        <p style="color: #666; font-size: 12px;">
          If you didn't request this, please ignore this email.
        </p>
    </div>
    """
    body_text = f"Reset your password: {reset_url}\nThis link expires in 30 minutes."
    sent = await get_email_sender().send(
        to=email, subject=subject, body_html=body_html, body_text=body_text
    )
    if not sent:
        logger.warning("Password recovery mail to user %d was not sent", user_id)
        redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
        try:
            await give_back_site_mail(redis)
        finally:
            await redis.aclose()


@router.post(
    "/recover/password/request",
    summary="Request password recovery",
)
async def recover_password_request(
    payload: ForgotPasswordRequest,
    request: Request,
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    import re

    from redis.asyncio import Redis as AsyncRedis

    from app.core.background import spawn
    from app.core.client_address import resolved_client_address
    from app.domain.user.mail_quota import MailQuota

    email = payload.email.strip()

    email_regex = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    if not re.match(email_regex, email):
        raise UnprocessableEntityError("Invalid email address format")

    # The quota is spent before the account is looked up, so it counts unknown
    # addresses exactly as it counts known ones; a request over it answers with
    # the same success body and simply sends nothing.
    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        claim = await MailQuota(redis, "password_recovery").claim(
            email, resolved_client_address(request)
        )
    finally:
        await redis.aclose()
    if not claim.granted:
        return _RECOVERY_REQUESTED

    user = await auth_service.get_user_by_email(email)
    if user is not None:
        spawn(
            _send_recovery_mail(user.id, email, user.username),
            name="password recovery mail",
        )
    return _RECOVERY_REQUESTED


@router.post(
    "/recover/password/verify",
    summary="Verify password recovery token",
)
async def recover_password_verify(
    payload: ResetPasswordRequest,
    auth_service: UserAuthService = Depends(get_user_auth_service),
    session: AsyncSession = Depends(get_db),
) -> dict:
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings
    from app.domain.user.login_security import PasswordResetService

    token = payload.token
    new_password = payload.password
    _require_new_password(new_password)

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        reset_service = PasswordResetService(redis)
        token_data = await reset_service.consume_reset_token(token)

        if not token_data:
            raise UnprocessableEntityError("Invalid or expired reset token")

        user_id = int(token_data["user_id"])
        await auth_service.update_password(user_id, new_password)
        # Whoever made the reset necessary may hold a sign-in; end them all.
        # Connector devices are not sign-ins and stay until removed.
        await SessionService(session).revoke_all(user_id, RevokeReason.PASSWORD_RESET)
        await TrustedDeviceService(session).revoke_all(user_id)

        return {
            "code": 200,
            "message": "Password reset successfully.",
        }
    finally:
        await redis.aclose()


@router.patch(
    "/{userId}/password",
    summary="Change password",
)
async def change_password(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    payload: ChangePasswordRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    current: uuid.UUID | None = Depends(get_current_session_id),
    auth_service: UserAuthService = Depends(get_user_auth_service),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Replace the account's password and sign out every other device.

    The device making the change stays signed in: it has just proved who is
    at it with the sudo ticket. No browser stays trusted to skip two-step
    verification, this one included.
    """
    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can change their password.")

    password = payload.password
    _require_new_password(password)

    await _spend_sudo_ticket(
        payload.sudo_ticket,
        user_id=auth_user.user_id,
        purpose=SudoPurpose.PASSWORD_CHANGE,
    )
    await auth_service.update_password(user_id, password)
    await SessionService(session).revoke_all(
        user_id, RevokeReason.PASSWORD_CHANGED, keep=current
    )
    await TrustedDeviceService(session).revoke_all(user_id)

    return {"code": 200, "message": "Password changed successfully"}


@router.get(
    "/{userId}/favorites/questions",
    summary="List user favorite questions",
)
async def get_user_favorite_questions(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    page_start: int | None = Query(default=None, alias="pageStart"),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    question_repo = QuestionRepository(session=db)
    topic_repo = QuestionTopicRepository(session=db)

    offset = page_start or 0
    rows, total = await question_repo.list_followed(
        user_id=user_id, limit=page_size, offset=offset
    )
    topic_map = await topic_repo.list_topic_ids([row.id for row in rows])

    questions = []
    for row in rows:
        created_at_ms = int(row.created_at.timestamp() * 1000) if row.created_at else 0
        updated_at_ms = int(row.updated_at.timestamp() * 1000) if row.updated_at else 0
        dto = {
            "id": row.id,
            "title": row.title,
            "content": None,
            "type": row.type,
            "groupId": row.group_id,
            "bounty": row.bounty,
            "acceptedAnswerId": row.accepted_answer_id,
            "createdBy": row.created_by_id,
            "createdAt": created_at_ms,
            "updatedAt": updated_at_ms,
            "topicIds": topic_map.get(row.id, []),
        }
        questions.append(dto)

    returned = len(questions)
    has_more = offset + returned < total
    next_start = offset + returned if has_more and returned > 0 else None
    page = {
        "pageStart": offset,
        "pageSize": returned,
        "hasMore": has_more,
        "nextStart": next_start,
        "total": total,
    }
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "questions": questions,
            "page": page,
        },
    }


@router.get(
    "/{userId}/favorites/answers",
    summary="List user favorite answers",
)
async def get_user_favorite_answers(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    page_start: int | None = Query(default=None, alias="pageStart"),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    answer_repo = AnswerRepository(session=db)
    profile_repo = UserProfileRepository(session=db)

    offset = page_start or 0
    rows, total = await answer_repo.list_favorites_by_user(
        user_id=user_id, limit=page_size, offset=offset
    )

    answers = []
    for row in rows:
        created_at_ms = int(row.created_at.timestamp() * 1000) if row.created_at else 0
        updated_at_ms = int(row.updated_at.timestamp() * 1000) if row.updated_at else 0
        profile = await profile_repo.get_profile_by_user_id(row.created_by_id)
        sender = None
        if profile:
            sender = {
                "id": profile.user_id,
                "nickname": profile.nickname,
                "avatarId": profile.avatar_id,
                "intro": profile.intro,
            }
        dto = {
            "id": row.id,
            "questionId": row.question_id,
            "content": row.content,
            "createdBy": row.created_by_id,
            "sender": sender,
            "createdAt": created_at_ms,
            "updatedAt": updated_at_ms,
        }
        answers.append(dto)

    returned = len(answers)
    has_more = offset + returned < total
    next_start = offset + returned if has_more and returned > 0 else None
    page = {
        "pageStart": offset,
        "pageSize": returned,
        "hasMore": has_more,
        "nextStart": next_start,
        "total": total,
    }
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "answers": answers,
            "page": page,
        },
    }


@router.get(
    "/{userId}/settings",
    summary="Get User Settings",
)
async def get_user_settings(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can view their settings.")
    settings = {
        "emailNotification": True,
        "pushNotification": True,
        "language": "zh-CN",
        "theme": "light",
    }
    return {"code": 200, "message": "OK", "data": {"settings": settings}}


@router.patch(
    "/{userId}/settings",
    summary="Update User Settings",
)
async def update_user_settings(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can update their settings.")
    settings = {
        "emailNotification": payload.get("emailNotification", True),
        "pushNotification": payload.get("pushNotification", True),
        "language": payload.get("language", "zh-CN"),
        "theme": payload.get("theme", "light"),
    }
    return {"code": 200, "message": "OK", "data": {"settings": settings}}


@router.get(
    "",
    summary="List users",
)
async def list_users(
    q: str | None = Query(default=None),
    page_start: int | None = Query(default=None, alias="pageStart"),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    """List users with optional search query."""
    profile_repo = UserProfileRepository(session=db)
    user_repo = UserRepository(session=db)
    follow_repo = UserFollowingRepository(session=db)
    stats_repo = UserStatisticsRepository(session=db)
    auth_service = UserAuthService(
        user_repo=user_repo,
        profile_repo=profile_repo,
        follow_repo=follow_repo,
        stats_repo=stats_repo,
    )

    offset = page_start or 0
    profiles = await profile_repo.list_profiles(limit=page_size, offset=offset)

    if q:
        filtered_profiles = []
        for profile in profiles:
            user = await user_repo.get_by_id(profile.user_id)
            if user and (
                q.lower() in user.username.lower()
                or q.lower() in profile.nickname.lower()
            ):
                filtered_profiles.append(profile)
        profiles = filtered_profiles

    users = []
    for profile in profiles:
        user = await user_repo.get_by_id(profile.user_id)
        if user:
            dto = await auth_service.build_user_dto(
                user=user,
                profile=profile,
                viewer_id=auth_user.user_id if auth_user.user_id > 0 else None,
            )
            users.append(dto)

    returned = len(users)
    page = {
        "pageStart": offset,
        "pageSize": returned,
        "hasMore": returned == page_size,
        "nextStart": offset + returned if returned == page_size else None,
    }
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "users": users,
            "page": page,
        },
    }


def _qr_data_uri(payload: str) -> str:
    """PNG data URI of a QR code (reference contract returns a ready-to-render
    <img src> value alongside the otpauth URL)."""
    import segno

    return segno.make(payload).png_data_uri(scale=5)


@router.post(
    "/{userId}/2fa/enable",
    summary="Start or confirm 2FA setup for user",
)
async def enable_user_2fa(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    payload: dict = Body(default={}),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    auth_service: UserAuthService = Depends(get_user_auth_service),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Two-phase, reference contract: no code → generate a secret and hand it
    to the client; {secret, code} → verify the live code against that secret,
    persist it, and return the one-time backup codes.

    The first phase spends a sudo ticket. The second needs none of its own:
    it accepts only the secret the first phase offered, so reaching it at all
    means having re-authenticated.
    """
    from app.domain.user.login_security import TOTPService

    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can enable 2FA.")

    secret = payload.get("secret")
    code = payload.get("code")

    totp_service = TOTPService(session)

    if await totp_service.is_2fa_enabled(auth_user.user_id):
        raise BadRequestError("2FA is already enabled")

    user, _profile = await auth_service.get_user_with_profile(auth_user.user_id)
    account_name = user.email or user.username

    if code:
        if not secret:
            raise BadRequestError("secret is required for confirmation")
        ok = await totp_service.enable_2fa(auth_user.user_id, secret, code)
        if not ok:
            raise UnprocessableEntityError("Invalid or expired verification code")
        backup_codes = await totp_service.generate_backup_codes(auth_user.user_id)
        # A browser trusted for an earlier factor has not proved this one.
        await TrustedDeviceService(session).revoke_all(auth_user.user_id)
        otpauth_url = totp_service.get_provisioning_uri(secret, account_name)
        return {
            "code": 201,
            "message": "2FA enabled successfully",
            "data": {
                "secret": secret,
                "otpauth_url": otpauth_url,
                "qrcode": _qr_data_uri(otpauth_url),
                "backup_codes": backup_codes,
            },
        }

    await _spend_sudo_ticket(
        payload.get("sudoTicket"),
        user_id=auth_user.user_id,
        purpose=SudoPurpose.TWO_FA_ENABLE,
    )
    new_secret = await totp_service.offer_secret(auth_user.user_id)
    otpauth_url = totp_service.get_provisioning_uri(new_secret, account_name)
    return {
        "code": 200,
        "message": "TOTP secret generated successfully",
        "data": {
            "secret": new_secret,
            "otpauth_url": otpauth_url,
            "qrcode": _qr_data_uri(otpauth_url),
            "backup_codes": [],
        },
    }


async def _notify_2fa_disabled(email: str | None, username: str) -> None:
    """Tell the account owner out of band that their second factor is gone.

    Out of band is the entire value. Everything else on this path — the
    session, the ticket, the screen that showed the confirmation — is already
    in the attacker's hands in the case worth defending against. Mail leaves
    through a channel they may not hold, which is what turns a silent lockout
    into something the owner can still catch and reverse.

    Never fatal. The factor is off by the time this runs, so raising here
    would answer a completed operation with an error and send the owner back
    to retry something that already happened.
    """
    if not email:
        return
    import html

    from app.core.email import get_email_sender

    subject = "[Cheese] Two-factor authentication was turned off"
    recover_url = f"{settings.frontend_url}/account/recover/password"
    # The username is user-chosen and this is an HTML document. Interpolating
    # it raw would let an account name carry markup into a mail the *owner*
    # opens — the one reader this message exists to reach honestly.
    safe_username = html.escape(username)
    body_html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <h2 style="color: #333;">Two-factor authentication was turned off</h2>
        <p>Hi {safe_username},</p>
        <p>
          Two-factor authentication has just been disabled on your Cheese
          account. Signing in now needs only your password.
        </p>
        <p>
          <strong>If this was not you</strong>, someone else is using your
          session. Change your password immediately and turn two-factor
          authentication back on:
        </p>
        <p><a href="{recover_url}" style="color: #007bff;">{recover_url}</a></p>
    </div>
    """
    body_text = (
        f"Hi {username},\n\n"
        "Two-factor authentication has just been disabled on your Cheese "
        "account. Signing in now needs only your password.\n\n"
        "If this was not you, someone else is using your session. Change your "
        f"password immediately and turn it back on: {recover_url}\n"
    )
    try:
        await get_email_sender().send(
            to=email, subject=subject, body_html=body_html, body_text=body_text
        )
    except Exception:
        logger.exception("2fa: could not notify %s that 2FA was disabled", email)


@router.post(
    "/{userId}/2fa/disable",
    summary="Disable 2FA for user",
)
async def disable_user_2fa(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    payload: SudoTicketRequest = Body(default_factory=SudoTicketRequest),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    auth_service: UserAuthService = Depends(get_user_auth_service),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Turn the second factor off, against a ticket and not just a session.

    Removing an MFA factor is a high-risk operation, so it is re-authenticated
    rather than merely authenticated: the caller has to present a sudo ticket
    minted for this one purpose, which they can only have got by proving a
    credential moments ago. A live session alone used to be enough, which made
    every other control on this account only as strong as the session cookie.
    """
    from app.domain.user.login_security import TOTPService

    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can disable 2FA.")

    totp_service = TOTPService(session)

    if not await totp_service.is_2fa_enabled(auth_user.user_id):
        raise BadRequestError("2FA is not enabled")

    await _spend_sudo_ticket(
        payload.sudo_ticket,
        user_id=auth_user.user_id,
        purpose=SudoPurpose.TWO_FA_DISABLE,
    )

    await totp_service.disable_2fa(auth_user.user_id)
    await TrustedDeviceService(session).revoke_all(auth_user.user_id)

    user, _profile = await auth_service.get_user_with_profile(auth_user.user_id)
    await _notify_2fa_disabled(user.email, user.username)

    return {
        "code": 200,
        "message": "2FA disabled successfully",
        "data": {"success": True},
    }


@router.get(
    "/{userId}/2fa/status",
    summary="Get 2FA status for user",
)
async def get_user_2fa_status(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    from app.domain.user.login_security import TOTPService

    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can view 2FA status.")

    totp_service = TOTPService(session)
    enabled = await totp_service.is_2fa_enabled(user_id)
    passkeys = await PasskeyRepository(session).list_by_user(user_id)

    return {
        "code": 200,
        "message": "Get 2FA status successfully",
        "data": {
            "enabled": enabled,
            "has_passkey": len(passkeys) > 0,
        },
    }


@router.post(
    "/{userId}/2fa/backup-codes",
    summary="Regenerate 2FA backup codes",
)
async def regenerate_backup_codes(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    payload: SudoTicketRequest = Body(default_factory=SudoTicketRequest),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    from app.domain.user.login_security import TOTPService

    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can manage backup codes.")

    totp_service = TOTPService(session)

    if not await totp_service.is_2fa_enabled(user_id):
        raise BadRequestError("2FA is not enabled")

    await _spend_sudo_ticket(
        payload.sudo_ticket,
        user_id=auth_user.user_id,
        purpose=SudoPurpose.TWO_FA_BACKUP_CODES,
    )
    backup_codes = await totp_service.generate_backup_codes(user_id)
    return {
        "code": 201,
        "message": "New backup codes generated successfully",
        "data": {"backup_codes": backup_codes},
    }


def _challenge_from_credential(credential: dict) -> str:
    """Recover the challenge echoed inside the WebAuthn clientDataJSON. The
    reference contract sends only the credential — the server must not trust a
    separately-supplied challenge anyway."""
    import base64
    import json as _json

    try:
        raw = credential["response"]["clientDataJSON"]
        padded = raw + "=" * (-len(raw) % 4)
        client_data = _json.loads(base64.urlsafe_b64decode(padded))
        challenge = client_data["challenge"]
        if not isinstance(challenge, str) or not challenge:
            raise KeyError("challenge")
        return challenge
    except (KeyError, TypeError, ValueError) as exc:
        raise BadRequestError("Malformed WebAuthn credential") from exc


@router.post(
    "/{userId}/passkeys/options",
    summary="Generate passkey registration options",
)
async def passkey_register_challenge(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    payload: SudoTicketRequest = Body(default_factory=SudoTicketRequest),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    auth_service: UserAuthService = Depends(get_user_auth_service),
    passkey_service: PasskeyService = Depends(get_passkey_service),
) -> dict:
    """Start registering a passkey, against a sudo ticket.

    Only this step spends the ticket. Completing the registration needs a
    challenge this step stored, so nothing can be registered without having
    come through here.
    """
    import json

    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings

    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can register a passkey.")

    await _spend_sudo_ticket(
        payload.sudo_ticket,
        user_id=auth_user.user_id,
        purpose=SudoPurpose.PASSKEY_ADD,
    )

    user, profile = await auth_service.get_user_with_profile(auth_user.user_id)

    options = await passkey_service.generate_registration_options(
        user_id=auth_user.user_id,
        username=user.username,
        display_name=profile.nickname if profile else user.username,
    )

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        challenge_key = f"passkey:challenge:{auth_user.user_id}:{options['challenge']}"
        await redis.set(
            challenge_key, json.dumps({"userId": auth_user.user_id}), ex=300
        )
    finally:
        await redis.aclose()

    return {
        "code": 200,
        "message": "OK",
        "data": {"options": options},
    }


@router.post(
    "/{userId}/passkeys",
    summary="Verify passkey registration",
)
async def passkey_register_verify(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    payload: dict = Body(default={}),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    passkey_service: PasskeyService = Depends(get_passkey_service),
    session: AsyncSession = Depends(get_db),
) -> dict:
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings

    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can register a passkey.")

    credential = payload.get("response")
    if not isinstance(credential, dict):
        raise BadRequestError("response (WebAuthn credential) is required")
    challenge = _challenge_from_credential(credential)

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        challenge_key = f"passkey:challenge:{auth_user.user_id}:{challenge}"
        stored = await redis.get(challenge_key)
        if not stored:
            raise BadRequestError("Invalid or expired challenge")
        await redis.delete(challenge_key)
    finally:
        await redis.aclose()

    result = await passkey_service.verify_registration(
        user_id=auth_user.user_id,
        challenge=challenge,
        credential=credential,
    )
    await PasskeyPromptService(session).end(auth_user.user_id)

    return {
        "code": 201,
        "message": "Passkey registered successfully.",
        "data": {"passkey": result},
    }


class PasskeyPromptDismissal(BaseModel):
    forever: bool = False


@router.post(
    "/{userId}/passkeys/prompt/dismiss",
    summary="Decline the offer to add a passkey",
)
async def dismiss_passkey_prompt(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    payload: PasskeyPromptDismissal = Body(default_factory=PasskeyPromptDismissal),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Hold the offer back for a while, or with ``forever`` stop it."""
    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can answer this offer.")

    await PasskeyPromptService(session).dismiss(user_id, forever=payload.forever)
    return {"code": 200, "message": "OK", "data": {}}


@router.post(
    "/auth/passkey/options",
    summary="Generate passkey authentication options",
)
async def passkey_authenticate_challenge(
    payload: dict = Body(default={}),
    passkey_service: PasskeyService = Depends(get_passkey_service),
) -> dict:
    import json

    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings

    user_id = payload.get("userId")

    options = await passkey_service.generate_authentication_options(
        user_id=user_id,
    )

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        challenge_key = f"passkey:auth_challenge:{options['challenge']}"
        data = {"userId": user_id} if user_id else {}
        await redis.set(challenge_key, json.dumps(data), ex=300)
    finally:
        await redis.aclose()

    return {
        "code": 200,
        "message": "OK",
        "data": {"options": options},
    }


@router.post(
    "/auth/passkey/verify",
    summary="Verify passkey authentication",
)
async def passkey_authenticate_verify(
    request: Request,
    response: Response,
    payload: dict = Body(default={}),
    passkey_service: PasskeyService = Depends(get_passkey_service),
    auth_service: UserAuthService = Depends(get_user_auth_service),
    session: AsyncSession = Depends(get_db),
) -> dict:
    from redis.asyncio import Redis as AsyncRedis

    from app.core.config import settings

    credential = payload.get("response")
    if not isinstance(credential, dict):
        raise BadRequestError("response (WebAuthn credential) is required")
    challenge = _challenge_from_credential(credential)

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        challenge_key = f"passkey:auth_challenge:{challenge}"
        stored = await redis.get(challenge_key)
        if not stored:
            raise BadRequestError("Invalid or expired challenge")
        await redis.delete(challenge_key)
    finally:
        await redis.aclose()

    user_id = await passkey_service.verify_authentication(
        challenge=challenge,
        credential=credential,
    )

    user, profile = await auth_service.get_user_with_profile(user_id)

    access_token = await issue_session(
        response,
        request,
        session,
        user_id=user_id,
        handle=user.username,
        login_method="passkey",
    )

    user_dto = await auth_service.build_user_dto(
        user=user,
        profile=profile,
        viewer_id=user_id,
    )
    return {
        "code": 201,
        "message": "Login successfully.",
        "data": {
            "user": user_dto,
            "accessToken": access_token,
        },
    }


@router.get(
    "/{userId}/passkeys",
    summary="List user passkeys",
)
async def list_passkeys(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    passkey_service: PasskeyService = Depends(get_passkey_service),
) -> dict:
    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can view their passkeys.")

    passkeys = await passkey_service.list_passkeys(user_id)

    return {
        "code": 200,
        "message": "OK",
        "data": {"passkeys": passkeys},
    }


@router.delete(
    "/{userId}/passkeys/{credentialId}",
    summary="Delete a passkey",
)
async def delete_passkey(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    credential_id: Annotated[str, Path(alias="credentialId")],
    payload: SudoTicketRequest = Body(default_factory=SudoTicketRequest),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    passkey_service: PasskeyService = Depends(get_passkey_service),
) -> dict:
    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can delete their passkeys.")

    await _spend_sudo_ticket(
        payload.sudo_ticket,
        user_id=auth_user.user_id,
        purpose=SudoPurpose.PASSKEY_DELETE,
    )

    deleted = await passkey_service.delete_passkey(user_id, credential_id)

    if not deleted:
        raise NotFoundError("Passkey not found")

    return {
        "code": 200,
        "message": "Passkey deleted successfully.",
    }


@router.get(
    "/auth/oauth/providers",
    summary="List available OAuth providers",
)
async def get_oauth_providers(
    oauth_service: OAuthService = Depends(get_oauth_service),
) -> dict:
    providers = oauth_service.get_providers_config()
    return {
        "code": 200,
        "message": "OK",
        "data": {"providers": providers},
    }


def _oauth_frontend_url(path: str, **params: str | None) -> str:
    """Build a frontend landing URL (success/error) for the browser redirect."""
    from urllib.parse import urlencode

    query = urlencode({k: v for k, v in params.items() if v is not None})
    return f"{settings.frontend_url}{path}" + (f"?{query}" if query else "")


# --- OAuth client-completion flow (reference contract) --------------------
# When the callback cannot resolve the account by itself it hands the browser
# to a frontend page with either a signed stateToken (decision page) or a
# Redis-backed pending session (credential-verify page). 15-minute TTL both,
# and each is redeemable once: the stateToken's ``jti`` is reserved when it is
# minted and claimed by whichever create/bind request spends it.

_OAUTH_STATE_TTL_S = 15 * 60
_OAUTH_STATE_SCOPE = "oauth_state"
_OAUTH_PENDING_PREFIX = "oauth:pending:"


async def _issue_oauth_state_token(provider_id: str, user_info: dict) -> str:
    """Mint a decision-page stateToken and reserve it for one redemption.

    Raises ``SingleUseUnavailableError`` when Redis is unreachable: a token we
    could not reserve would be refused by every endpoint that spends it.
    """
    import time
    import uuid

    from app.core.single_use_state import reserve

    now = int(time.time())
    jti = uuid.uuid4().hex
    token = jwt.encode(
        {
            "type": "oauth_state",
            "provider": provider_id,
            "info": user_info,
            "jti": jti,
            "iat": now,
            "exp": now + _OAUTH_STATE_TTL_S,
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    await reserve(_OAUTH_STATE_SCOPE, jti, ttl_s=_OAUTH_STATE_TTL_S)
    return token


def _decode_oauth_state_token(token: str) -> tuple[str, dict, str]:
    """(provider, userInfo, jti). Reading does not spend the token."""
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise AuthenticationRequiredError(
            "Invalid or expired OAuth state token"
        ) from exc
    jti = claims.get("jti")
    if claims.get("type") != "oauth_state" or not isinstance(jti, str) or not jti:
        raise AuthenticationRequiredError("Invalid or expired OAuth state token")
    return str(claims["provider"]), dict(claims["info"]), jti


def _reissue_oauth_state_token(token: str, **info: str) -> str:
    """The same stateToken — same ``jti``, same expiry — with ``info`` added
    to its userInfo. Spending either one spends both."""
    claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    claims["info"] = {**claims["info"], **info}
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


async def _redeem_oauth_state_token(jti: str) -> bool:
    """Spend a decoded stateToken. True exactly once; fails closed."""
    from app.core.single_use_state import SingleUseUnavailableError, claim

    try:
        return await claim(_OAUTH_STATE_SCOPE, jti)
    except SingleUseUnavailableError:
        logger.exception("oauth: cannot claim state token")
        return False


async def _spend_oauth_password_attempt(username: str) -> bool:
    """Count one attempt against ``username`` before a credential check.
    False while the wait earlier failures started is running, and the request
    must be refused.

    The same count password login uses, so proving a password through an
    OAuth binding page cannot skip the wait.
    """
    from redis.asyncio import Redis as AsyncRedis

    from app.domain.user.login_security import LoginDelay

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        return (await LoginDelay(redis).admit(username)).admitted
    finally:
        await redis.aclose()


async def _clear_oauth_password_attempts(username: str) -> None:
    from redis.asyncio import Redis as AsyncRedis

    from app.domain.user.login_security import LoginDelay

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        await LoginDelay(redis).clear(username)
    finally:
        await redis.aclose()


def _oauth_too_many_attempts_redirect() -> RedirectResponse:
    return _oauth_error_redirect(
        "TOO_MANY_ATTEMPTS", "Too many failed attempts. Try again later"
    )


def _oauth_user_info_dict(user_info) -> dict:
    return {
        "id": user_info.id,
        "email": user_info.email,
        "name": user_info.name,
        "username": user_info.username,
        "preferredUsername": user_info.preferred_username,
    }


async def _store_oauth_pending(session_id: str, data: dict) -> None:
    import json

    from redis.asyncio import Redis as AsyncRedis

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=True)
    try:
        await redis.setex(
            f"{_OAUTH_PENDING_PREFIX}{session_id}", _OAUTH_STATE_TTL_S, json.dumps(data)
        )
    finally:
        await redis.aclose()


async def _pop_oauth_pending(session_id: str) -> dict | None:
    """Fetch-and-delete (one-shot; replay protection)."""
    import json

    from redis.asyncio import Redis as AsyncRedis

    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=True)
    try:
        key = f"{_OAUTH_PENDING_PREFIX}{session_id}"
        pipe = redis.pipeline()
        pipe.get(key)
        pipe.delete(key)
        raw, _ = await pipe.execute()
        return json.loads(raw) if raw else None
    finally:
        await redis.aclose()


async def _start_oauth_ownership(
    provider_id: str, user_info: dict, owner
) -> dict[str, str]:
    """Hand an identity whose email belongs to ``owner`` to the verify page,
    where the person proves they hold that account before it is linked —
    never linked on the email alone. Returns the verify page's query."""
    import secrets
    import time

    session_id = (
        f"oauth_password_{provider_id}_{user_info.get('id')}_"
        f"{int(time.time() * 1000)}_{secrets.token_hex(4)}"
    )
    await _store_oauth_pending(
        session_id,
        {
            "type": "password",
            "providerId": provider_id,
            "userInfo": user_info,
            "userId": owner.id,
            "username": owner.username,
        },
    )
    return {"type": "password", "email": owner.username, "sessionId": session_id}


async def _suggest_oauth_identity(auth_service, user_info: dict) -> tuple[str, str]:
    """(suggestedUsername, suggestedNickname), both passing the rules the
    create form is checked against, the username de-duplicated."""
    import secrets as _secrets

    base_raw = (
        user_info.get("preferredUsername")
        or user_info.get("username")
        or user_info.get("name")
        or f"user_{user_info.get('id')}"
    )
    suffix_length = 7  # "_" + token_hex(3)
    base = (
        "".join(
            ch for ch in str(base_raw) if ch.isascii() and (ch.isalnum() or ch in "_-")
        )[: USERNAME_MAX_LENGTH - suffix_length]
        or "user"
    )
    username = base
    # A reserved name is suggested exactly as readily as a taken one — the
    # provider's `preferredUsername` could be "system" — so treat it the same
    # way here instead of letting the user hit the wall on submit (#345).
    while (
        not is_valid_username(username)
        or await auth_service.is_username_taken(username)
        or is_reserved_username(username)
    ):
        username = f"{base}_{_secrets.token_hex(3)}"
    raw_nickname = str(
        user_info.get("name") or user_info.get("preferredUsername") or username
    )
    try:
        nickname = normalize_nickname(raw_nickname[:NICKNAME_MAX_LENGTH])
    except UnprocessableEntityError:
        nickname = username
    return username, nickname


async def _oauth_login_redirect(
    request: Request,
    session: AsyncSession,
    auth_service,
    user_id: int,
    provider_id: str,
    **extra: str | None,
) -> RedirectResponse:
    """Sign in a resolved OAuth login and land on the success page.

    An account with 2FA gets a 2FA ticket and the verify page instead, exactly
    as a password login would: the provider stands in for the password only,
    and a trusted browser skips the second step here as it does there.
    """
    from app.domain.user.login_security import TOTPService

    trust = None
    if await TOTPService(session).is_2fa_enabled(user_id):
        trust = await _trusted_device(request, session, user_id)
        if trust is None:
            return RedirectResponse(
                _oauth_frontend_url(
                    settings.frontend_2fa_verify_path,
                    token=await _issue_2fa_pending_token(user_id),
                ),
                status_code=302,
            )

    user_obj, _profile = await auth_service.get_user_with_profile(user_id)
    redirect = RedirectResponse(
        _oauth_frontend_url(
            settings.frontend_oauth_success_path,
            email=user_obj.email or user_obj.username,
            provider=provider_id,
            **extra,
        ),
        status_code=302,
    )
    # Only the refresh cookie rides the redirect. A token in the URL would
    # land in the browser history, the Referer header and access logs; the
    # landing page trades the cookie for one instead.
    await issue_session(
        redirect,
        request,
        session,
        user_id=user_id,
        handle=user_obj.username,
        login_method=f"oauth:{provider_id}",
        trust=trust,
    )
    return redirect


def _oauth_error_redirect(error_code: str, message: str) -> RedirectResponse:
    return RedirectResponse(
        _oauth_frontend_url(
            settings.frontend_oauth_error_path, error_code=error_code, error=message
        ),
        status_code=302,
    )


# The login `state` is ours, never the client's. It is reserved server-side so
# the callback can spend it once, and bound to the browser that started the
# flow by an httpOnly cookie: a callback URL carried to another browser arrives
# without the cookie and is refused. SameSite=Lax still sends the cookie on the
# provider's top-level GET redirect back to us.
_OAUTH_LOGIN_STATE_COOKIE = "OAUTH_STATE"
_OAUTH_LOGIN_STATE_SCOPE = "oauth_login_state"
_OAUTH_LOGIN_STATE_TTL_S = 10 * 60


def _oauth_login_state_scope(provider_id: str) -> str:
    # Per provider, so a state issued for one provider cannot be spent at
    # another provider's callback.
    return f"{_OAUTH_LOGIN_STATE_SCOPE}:{provider_id}"


async def _spend_oauth_login_state(
    provider_id: str, state: str | None, cookie_state: str | None
) -> bool:
    """True when ``state`` is the one this browser was issued and is unspent."""
    import hmac

    from app.core.single_use_state import SingleUseUnavailableError, claim

    if not state or not cookie_state:
        return False
    if not hmac.compare_digest(state.encode(), cookie_state.encode()):
        return False
    try:
        return await claim(_oauth_login_state_scope(provider_id), state)
    except SingleUseUnavailableError:
        logger.exception("OAuth callback: cannot claim login state for %s", provider_id)
        return False


def _oauth_callback_error_redirect(provider_id: str, message: str) -> RedirectResponse:
    return RedirectResponse(
        _oauth_frontend_url(
            settings.frontend_oauth_error_path, message=message, provider=provider_id
        ),
        status_code=302,
    )


@router.get(
    "/auth/oauth/login/{providerId}",
    summary="Redirect to the OAuth provider's authorization page",
)
async def get_oauth_login_url(
    provider_id: Annotated[str, Path(alias="providerId")],
    oauth_service: OAuthService = Depends(get_oauth_service),
) -> RedirectResponse:
    # The frontend navigates the browser straight to this endpoint, so we
    # 302-redirect to the provider's authorization page.
    import secrets
    from urllib.parse import urlparse

    from app.core.single_use_state import SingleUseUnavailableError, reserve

    state = secrets.token_urlsafe(32)
    try:
        provider = oauth_service.get_provider(provider_id)
        auth_url = oauth_service.generate_authorization_url(provider_id, state)
    except NotFoundError:
        raise NotFoundError(
            f"OAuth provider '{provider_id}' not found or not enabled"
        ) from None

    try:
        await reserve(
            _oauth_login_state_scope(provider_id),
            state,
            ttl_s=_OAUTH_LOGIN_STATE_TTL_S,
        )
    except SingleUseUnavailableError:
        logger.exception("OAuth login: cannot reserve state for %s", provider_id)
        return _oauth_callback_error_redirect(provider_id, "oauth_failed")

    redirect = RedirectResponse(auth_url, status_code=302)
    redirect.set_cookie(
        _OAUTH_LOGIN_STATE_COOKIE,
        state,
        max_age=_OAUTH_LOGIN_STATE_TTL_S,
        httponly=True,
        secure=settings.environment not in ("development", "test"),
        samesite="lax",
        # The provider sends the browser to exactly this URL, so its path is
        # the callback path as the browser sees it, gateway mount included.
        path=urlparse(provider.config.redirect_url).path or "/",
    )
    return redirect


@router.get(
    "/auth/oauth/callback/{providerId}",
    summary="Handle OAuth callback and log the user in",
)
async def handle_oauth_callback(
    provider_id: Annotated[str, Path(alias="providerId")],
    request: Request,
    code: str = Query(...),
    state: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    oauth_service: OAuthService = Depends(get_oauth_service),
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> RedirectResponse:
    # Step 0: the state must be the one issued to this browser, unspent —
    # checked before the code is exchanged.
    if not await _spend_oauth_login_state(
        provider_id, state, request.cookies.get(_OAUTH_LOGIN_STATE_COOKIE)
    ):
        logger.info("OAuth callback: state rejected for %s", provider_id)
        return _oauth_callback_error_redirect(provider_id, "invalid_state")

    # Step 1: exchange the code and fetch the provider profile. Any failure here
    # is an authentication problem — bounce to the frontend error page.
    try:
        _access_token, user_info = await oauth_service.handle_callback(
            provider_id=provider_id,
            code=code,
        )
    except Exception:
        logger.exception("OAuth callback: provider exchange failed for %s", provider_id)
        return _oauth_callback_error_redirect(provider_id, "oauth_failed")

    # Step 2 — resolve the local account (reference contract):
    #   A. existing binding → straight to the success page.
    #   B. the provider email already belongs to a local account → the user must
    #      prove ownership on the verify page (Redis pending session — never
    #      silently link by email).
    #   C. unknown identity → the decision page (signed stateToken) lets the
    #      user create an account or bind an existing one.
    try:
        existing = await oauth_service.get_connection_by_provider(
            provider_id=provider_id,
            provider_user_id=user_info.id,
        )
        if existing:
            return await _oauth_login_redirect(
                request, session, auth_service, existing["userId"], provider_id
            )

        info_dict = _oauth_user_info_dict(user_info)

        conflict_user = None
        if user_info.email:
            conflict_user = await auth_service.get_user_by_email(user_info.email)

        if conflict_user is not None:
            return RedirectResponse(
                _oauth_frontend_url(
                    settings.frontend_oauth_verify_path,
                    **await _start_oauth_ownership(
                        provider_id, info_dict, conflict_user
                    ),
                ),
                status_code=302,
            )

        state_token = await _issue_oauth_state_token(provider_id, info_dict)
        return RedirectResponse(
            _oauth_frontend_url(
                settings.frontend_oauth_complete_path, stateToken=state_token
            ),
            status_code=302,
        )
    except Exception:
        await session.rollback()
        logger.exception(
            "OAuth callback: account resolution failed for %s", provider_id
        )
        return RedirectResponse(
            _oauth_frontend_url(
                settings.frontend_oauth_error_path,
                message="account_error",
                provider=provider_id,
            ),
            status_code=302,
        )


@router.get(
    "/auth/oauth/state",
    summary="Decode the OAuth decision-page state token",
)
async def get_oauth_state(
    token: str = Query(...),
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    provider_id, user_info, _jti = _decode_oauth_state_token(token)
    suggested_username, suggested_nickname = await _suggest_oauth_identity(
        auth_service, user_info
    )
    return {
        "code": 200,
        "message": "Get OAuth state successfully.",
        "data": {
            "providerId": provider_id,
            "userInfo": user_info,
            "suggestedUsername": suggested_username,
            "suggestedNickname": suggested_nickname,
        },
    }


@router.post(
    "/auth/oauth/email/code",
    summary="Send a code to the email a new OAuth account will hold",
)
async def send_oauth_email_code(
    payload: OAuthEmailCodeRequest, request: Request
) -> dict:
    _decode_oauth_state_token(payload.state_token)
    await _send_email_code(request, _own_email(payload.email))
    return {"code": 200, "message": "Verification code sent."}


@router.post(
    "/auth/oauth/email/verify",
    summary="Verify the email a new OAuth account will hold",
)
async def verify_oauth_email(
    payload: OAuthEmailVerifyRequest,
    request: Request,
    auth_service: UserAuthService = Depends(get_user_auth_service),
) -> dict:
    """Answers with either the stateToken again, now carrying the verified
    address that account creation requires, or — when the address belongs to
    an account already — the verify page where that account is proven and
    linked, exactly as for a provider email that matches one."""
    provider_id, user_info, jti = _decode_oauth_state_token(payload.state_token)
    email = _own_email(payload.email)
    await _check_email_code(request, email, payload.code)

    owner = await auth_service.get_user_by_email(email)
    if owner is None:
        return {
            "code": 200,
            "message": "Email verified.",
            "data": {
                "stateToken": _reissue_oauth_state_token(
                    payload.state_token, verifiedEmail=email
                )
            },
        }
    if not await _redeem_oauth_state_token(jti):
        raise AuthenticationRequiredError("Invalid or expired OAuth state token")
    return {
        "code": 200,
        "message": "Email belongs to an existing account.",
        "data": {
            "ownership": await _start_oauth_ownership(provider_id, user_info, owner)
        },
    }


async def _complete_oauth_binding(
    *,
    request: Request,
    session: AsyncSession,
    auth_service: UserAuthService,
    oauth_service: OAuthService,
    user_id: int,
    provider_id: str,
    user_info: dict,
    **extra: str | None,
) -> RedirectResponse:
    """Create the provider↔user connection (idempotence guard) and log in.

    When the identity belongs to someone else, everything this request wrote
    is rolled back, so an account created for the binding does not outlive it.
    """
    provider_user_id = str(user_info.get("id"))
    existing = await oauth_service.get_connection_by_provider(
        provider_id=provider_id, provider_user_id=provider_user_id
    )
    if existing is None:
        try:
            await oauth_service.create_connection(
                user_id=user_id,
                provider_id=provider_id,
                provider_user_id=provider_user_id,
                raw_profile={
                    "email": user_info.get("email"),
                    "name": user_info.get("name"),
                },
            )
        except ConflictError:
            # Linked by a concurrent request since the lookup above.
            existing = await oauth_service.get_connection_by_provider(
                provider_id=provider_id, provider_user_id=provider_user_id
            )
    if existing is not None and existing["userId"] != user_id:
        await session.rollback()
        return _oauth_error_redirect(
            "ALREADY_LINKED", "This OAuth account is linked to another user"
        )
    return await _oauth_login_redirect(
        request, session, auth_service, user_id, provider_id, **extra
    )


@router.post(
    "/auth/oauth/verify",
    summary="Prove ownership of an email-conflicting account (verify page)",
)
async def oauth_verify_conflict(
    request: Request,
    payload: dict = Body(default={}),
    session: AsyncSession = Depends(get_db),
    auth_service: UserAuthService = Depends(get_user_auth_service),
    oauth_service: OAuthService = Depends(get_oauth_service),
) -> RedirectResponse:
    """Reference contract: responds with a 302 on success AND failure — the
    verify page follows the redirect to the success/error landing page."""
    session_id = payload.get("sessionId") or ""
    pending = await _pop_oauth_pending(session_id) if session_id else None
    if not pending or pending.get("type") != "password":
        return _oauth_error_redirect("SESSION_EXPIRED", "OAuth session expired")

    user_id = int(pending["userId"])
    if not await _spend_oauth_password_attempt(pending["username"]):
        return _oauth_too_many_attempts_redirect()
    try:
        password = payload.get("password") or ""
        user, _profile = await auth_service.get_user_with_profile(user_id)
        if not await auth_service.verify_password(user, password):
            return _oauth_error_redirect("INVALID_PASSWORD", "Invalid password")

        await _clear_oauth_password_attempts(pending["username"])
        return await _complete_oauth_binding(
            request=request,
            session=session,
            auth_service=auth_service,
            oauth_service=oauth_service,
            user_id=user_id,
            provider_id=pending["providerId"],
            user_info=pending["userInfo"],
            linked="true",
        )
    except Exception:
        await session.rollback()
        logger.exception("OAuth verify: binding failed")
        return _oauth_error_redirect("VERIFICATION_FAILED", "Verification failed")


@router.post(
    "/oauth/create",
    summary="Create a new account from the OAuth decision page (form post)",
)
async def oauth_create_user(
    request: Request,
    stateToken: str = Form(...),
    username: str = Form(...),
    nickname: str = Form(...),
    passwordMode: str = Form(default="none"),
    password: str | None = Form(default=None),
    inviteCode: str | None = Form(default=None),
    consentTerms: str | None = Form(default=None),
    consentPrivacy: str | None = Form(default=None),
    consentMethod: str | None = Form(default=None),
    session: AsyncSession = Depends(get_db),
    auth_service: UserAuthService = Depends(get_user_auth_service),
    oauth_service: OAuthService = Depends(get_oauth_service),
) -> RedirectResponse:
    try:
        provider_id, user_info, jti = _decode_oauth_state_token(stateToken)
    except AuthenticationRequiredError:
        return _oauth_error_redirect("TOKEN_EXPIRED", "Session expired")
    # Set only by /auth/oauth/email/verify: every account starts with an
    # address it can be recovered through, proven by a code sent to it.
    email = user_info.get("verifiedEmail")
    if not email:
        return _oauth_error_redirect("EMAIL_UNVERIFIED", "Email not verified")

    if not is_valid_username(username):
        return _oauth_error_redirect("INVALID_USERNAME", "Invalid username format")
    try:
        nickname = normalize_nickname(nickname)
    except UnprocessableEntityError as exc:
        return _oauth_error_redirect("INVALID_NICKNAME", str(exc))
    if passwordMode not in ("none", "password"):
        return _oauth_error_redirect("INVALID_AUTH_MODE", "Invalid auth mode")
    consent = {
        k: v
        for k, v in (("terms", consentTerms), ("privacy", consentPrivacy))
        if v is not None
    }
    consent_problem = _signup_consent(consent, consentMethod)
    if consent_problem:
        return _oauth_error_redirect("CONSENT_REQUIRED", consent_problem)
    assert consentMethod is not None
    if passwordMode == "password":
        try:
            _require_new_password(password or "")
        except (BadRequestError, UnprocessableEntityError) as exc:
            return _oauth_error_redirect("WEAK_PASSWORD", str(exc))
    # Same gate as /users registration: an OAuth account is still a new account.
    try:
        invite_code = _normalize_registration_invite_code(
            inviteCode, required=settings.require_invite_code
        )
    except UnprocessableEntityError as exc:
        return _oauth_error_redirect("INVITE_CODE_REQUIRED", str(exc))
    invite_service = InviteCodeService(session)
    if invite_code:
        try:
            await invite_service.validate_code(invite_code)
        except ValueError as exc:
            return _oauth_error_redirect(
                "INVALID_INVITE_CODE", str(_invite_code_error(exc))
            )
    if is_reserved_username(username):
        # Ahead of the try/except below on purpose: that block catches
        # Exception broadly and answers with a generic "creation failed" plus an
        # exception log, which is the wrong shape for a name the user can simply
        # change (#345).
        return _oauth_error_redirect("USERNAME_RESERVED", "Username is reserved")
    if await auth_service.is_username_taken(username):
        return _oauth_error_redirect("USERNAME_TAKEN", "Username already taken")
    if not await _redeem_oauth_state_token(jti):
        return _oauth_error_redirect("TOKEN_EXPIRED", "Session expired")
    # Before the account is created, not after: a second stateToken for an
    # identity linked meanwhile would otherwise leave an account behind with
    # nothing linked to it.
    if await oauth_service.get_connection_by_provider(
        provider_id=provider_id, provider_user_id=str(user_info.get("id"))
    ):
        return _oauth_error_redirect(
            "ALREADY_LINKED", "This OAuth account is linked to another user"
        )
    # Registered by someone else since it was verified.
    owner = await auth_service.get_user_by_email(email)
    if owner is not None:
        return RedirectResponse(
            _oauth_frontend_url(
                settings.frontend_oauth_verify_path,
                **await _start_oauth_ownership(provider_id, user_info, owner),
            ),
            status_code=302,
        )

    try:
        try:
            user, _profile = await auth_service.register_oauth_decision(
                email=email,
                username=username,
                nickname=nickname,
                password=password if passwordMode == "password" else None,
            )
        except ValueError as exc:
            # Lost a race the checks above could not see.
            if str(exc) == "USERNAME_TAKEN":
                return _oauth_error_redirect("USERNAME_TAKEN", "Username already taken")
            if str(exc) == "EMAIL_TAKEN":
                return _oauth_error_redirect("EMAIL_TAKEN", "Email already registered")
            raise
        ip, user_agent = client_context(request)
        await ConsentService(session).record(
            user_id=user.id,
            accepted=consent,
            method=consentMethod,
            entry="oauth_signup",
            ip=ip,
            user_agent=user_agent,
        )
        if invite_code:
            try:
                await invite_service.consume_code(invite_code)
            except ValueError as exc:
                await session.rollback()
                return _oauth_error_redirect(
                    "INVALID_INVITE_CODE", str(_invite_code_error(exc))
                )
        response = await _complete_oauth_binding(
            request=request,
            session=session,
            auth_service=auth_service,
            oauth_service=oauth_service,
            user_id=user.id,
            provider_id=provider_id,
            user_info=user_info,
            created="true",
            authMode=passwordMode,
        )
        # The landing page signs in and asks for pending consents as soon as it
        # follows this redirect, which dependency teardown would commit after.
        await session.commit()
        return response
    except Exception:
        await session.rollback()
        logger.exception("OAuth create: account creation failed")
        return _oauth_error_redirect("CREATION_FAILED", "Account creation failed")


@router.post(
    "/oauth/bind",
    summary="Bind OAuth to an existing account (decision page, form post)",
)
async def oauth_bind_user(
    request: Request,
    stateToken: str = Form(...),
    username: str = Form(...),
    password: str = Form(default=""),
    session: AsyncSession = Depends(get_db),
    auth_service: UserAuthService = Depends(get_user_auth_service),
    oauth_service: OAuthService = Depends(get_oauth_service),
) -> RedirectResponse:
    try:
        provider_id, user_info, jti = _decode_oauth_state_token(stateToken)
    except AuthenticationRequiredError:
        return _oauth_error_redirect("TOKEN_EXPIRED", "Session expired")
    if not await _redeem_oauth_state_token(jti):
        return _oauth_error_redirect("TOKEN_EXPIRED", "Session expired")

    if not await _spend_oauth_password_attempt(username):
        return _oauth_too_many_attempts_redirect()

    user = await auth_service._user_repo.get_by_username(username)
    # Unknown users and wrong passwords get the same answer.
    if user is None or not await auth_service.verify_password(user, password):
        return _oauth_error_redirect("INVALID_CREDENTIALS", "Invalid credentials")
    await _clear_oauth_password_attempts(username)

    try:
        return await _complete_oauth_binding(
            request=request,
            session=session,
            auth_service=auth_service,
            oauth_service=oauth_service,
            user_id=user.id,
            provider_id=provider_id,
            user_info=user_info,
            bound="true",
        )
    except Exception:
        await session.rollback()
        logger.exception("OAuth bind: binding failed")
        return _oauth_error_redirect("BINDING_FAILED", "Binding failed")


# ── Invite Code Management ──────────────────────────────────────────────


@router.get(
    "/invite-codes",
    summary="List invite codes (admin)",
)
async def list_invite_codes(
    auth_user: AuthUserInfo = Depends(require_auth_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    from app.domain.invite.services import InviteCodeService

    service = InviteCodeService(session)
    codes = await service.list_codes()
    return {
        "code": 200,
        "message": "Success",
        "data": {
            "codes": [
                {
                    "id": c.id,
                    "code": c.code,
                    "maxUses": c.max_uses,
                    "useCount": c.use_count,
                    "isActive": c.is_active,
                    "createdBy": c.created_by,
                    "note": c.note,
                    "createdAt": c.created_at.isoformat() if c.created_at else None,
                    "expiresAt": c.expires_at.isoformat() if c.expires_at else None,
                }
                for c in codes
            ]
        },
    }


@router.post(
    "/invite-codes",
    summary="Create invite code (admin)",
)
async def create_invite_code(
    payload: CreateInviteCodeRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    from app.domain.invite.services import InviteCodeService

    service = InviteCodeService(session)
    invite = await service.create_code(
        max_uses=payload.max_uses,
        created_by=auth_user.user_id,
        note=payload.note,
    )
    await session.commit()
    return {
        "code": 201,
        "message": "Invite code created.",
        "data": {
            "code": invite.code,
            "id": invite.id,
            "maxUses": invite.max_uses,
        },
    }


@router.delete(
    "/invite-codes/{code_id}",
    summary="Deactivate invite code (admin)",
)
async def deactivate_invite_code(
    code_id: int,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    from app.domain.invite.services import InviteCodeService

    service = InviteCodeService(session)
    await service.deactivate_code(code_id)
    await session.commit()
    return {"code": 200, "message": "Invite code deactivated.", "data": None}


@router.get(
    "/{userId}/oauth/connections",
    summary="List user OAuth connections",
)
async def list_oauth_connections(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    oauth_service: OAuthService = Depends(get_oauth_service),
) -> dict:
    if auth_user.user_id != user_id:
        raise ForbiddenError(
            "Only the user themselves can view their OAuth connections."
        )

    connections = await oauth_service.list_user_connections(user_id)

    return {
        "code": 200,
        "message": "OK",
        "data": {"connections": connections},
    }


@router.delete(
    "/{userId}/oauth/connections/{connectionId}",
    summary="Unbind OAuth connection",
)
async def delete_oauth_connection(
    user_id: Annotated[int, Path(ge=0, alias="userId")],
    connection_id: Annotated[int, Path(alias="connectionId")],
    payload: SudoTicketRequest = Body(default_factory=SudoTicketRequest),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    oauth_service: OAuthService = Depends(get_oauth_service),
) -> dict:
    if auth_user.user_id != user_id:
        raise ForbiddenError(
            "Only the user themselves can unbind their OAuth connections."
        )

    await _spend_sudo_ticket(
        payload.sudo_ticket,
        user_id=auth_user.user_id,
        purpose=SudoPurpose.OAUTH_UNBIND,
    )

    deleted = await oauth_service.delete_connection(connection_id, user_id)

    if not deleted:
        raise NotFoundError("OAuth connection not found")

    return {
        "code": 200,
        "message": "OAuth connection removed successfully.",
    }
