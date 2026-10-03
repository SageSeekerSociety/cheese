"""登录、两步验证、邮件验证码登录、刷新令牌、登出、sudo 提权。"""

import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from fastapi import (
    APIRouter,
    Depends,
    Request,
    Response,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_passkey_service,
    get_user_auth_service,
)
from app.api.routes.users_common import (
    _SUDO_TICKET_SCOPE,
    REFRESH_COOKIE,
    _clear_refresh_cookie,
    _set_refresh_cookie,
    challenge_from_credential,
    issue_session,
)
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.common.auth import (
    SudoPurpose,
    create_access_token,
    get_current_session_id,
)
from app.core.config import settings
from app.core.email import is_placeholder_email
from app.core.errors import (
    AuthenticationRequiredError,
    BadRequestError,
    ForbiddenError,
    InternalServerError,
    SudoRequiredError,
    UnprocessableEntityError,
)
from app.core.sentences import say
from app.db.session import get_db
from app.domain.passkey.prompt import PasskeyPromptService
from app.domain.passkey.services import PasskeyService
from app.domain.user.services import (
    UserAuthService,
)
from app.domain.user.sessions import SessionService

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from app.domain.user.login_security import LoginDelay

from app.api.routes.users._common import (
    _EMAIL_FORMAT,
    _PENDING_2FA_SCOPE,
    EmailCodeSignInRequest,
    EmailCodeSignInVerifyRequest,
    LoginRequest,
    SudoAuthRequest,
    _email_code_confirms,
    _email_code_unavailable,
    _issue_2fa_pending_token,
    _too_many_from_client,
    _trusted_device,
    logger,
)

router = APIRouter(prefix="/users", tags=["Users"])


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
        raise InternalServerError(say("securityCheckUnavailable")) from None
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
            trust_device_id=trust.id if trust else None,
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
        raise InternalServerError(say("twoFactorUnavailable")) from None
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
        trust_device_id=trust.id if trust else None,
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
        challenge = challenge_from_credential(credential)

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
