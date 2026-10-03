"""注册与注册邮箱验证码。"""

from typing import TYPE_CHECKING

from fastapi import (
    APIRouter,
    Depends,
    Request,
    Response,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_user_auth_service,
)
from app.api.routes.legal import client_context
from app.api.routes.users_common import (
    issue_session,
)
from app.core.config import settings
from app.core.errors import (
    BadRequestError,
    ConflictError,
    UnprocessableEntityError,
)
from app.core.sentences import say
from app.db.session import get_db
from app.domain.identity.handles import is_reserved_username
from app.domain.legal.services import ConsentService
from app.domain.user.passwords import require_new_password
from app.domain.user.repositories import (
    UserRepository,
)
from app.domain.user.services import (
    UserAuthService,
    is_valid_username,
    normalize_nickname,
)

if TYPE_CHECKING:
    pass

from app.api.routes.users._common import (
    RegisterUserRequest,
    SendEmailCodeRequest,
    _invite_code_error,
    _normalize_registration_invite_code,
    _signup_consent,
    _too_many_from_client,
)

router = APIRouter(prefix="/users", tags=["Users"])


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

    client = resolved_client_address(request)
    redis = AsyncRedis.from_url(settings.redis_url, decode_responses=False)
    try:
        # The account lookup sits behind the same client budget that guessing
        # mailed codes spends, for the reason the sign-in endpoint gives: one
        # source asking "is this address taken?" is the same attacker whichever
        # form it types into. Before this, the 409 below never touched Redis —
        # it was free — so the question could be asked as fast as the socket
        # allowed. A probe that finds a registered address now spends a slot
        # and keeps it; a request that goes on to mail a code hands it back.
        from app.domain.user.login_security import ClientFailureBudget

        budget = ClientFailureBudget(redis, "email_code")
        wait = await budget.spend(client)
        if wait:
            raise _too_many_from_client(wait)
        mailed = False
        try:
            user_repo = UserRepository(session=db)
            if await user_repo.is_email_taken(email):
                raise ConflictError("Email already registered")
            await EmailVerificationService(redis).send_verification_code(email, client)
            mailed = True
        finally:
            if mailed:
                await budget.refund(client)
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
        raise UnprocessableEntityError(say("usernameReserved"))

    nickname = normalize_nickname(nickname)

    require_new_password(password)

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
            raise UnprocessableEntityError(say("usernameReserved")) from exc
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
