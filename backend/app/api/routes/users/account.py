"""`/users/me` 一族，以及 `/{userId}` 的资料读改。"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated

from fastapi import (
    APIRouter,
    Body,
    Depends,
    Path,
    Request,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_user_auth_service,
)
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.common.auth import (
    get_current_session_id,
)
from app.core.config import settings
from app.core.email import is_placeholder_email
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.db.session import get_db
from app.domain.user.models import (
    UserSession,
)
from app.domain.user.repositories import (
    UserProfileRepository,
    UserRepository,
)
from app.domain.user.services import (
    UserAuthService,
    UserProfileService,
)

if TYPE_CHECKING:
    pass

from redis.asyncio import Redis as AsyncRedis
from sqlalchemy.exc import IntegrityError

from app.api.routes.users._common import (
    AddEmailCodeRequest,
    AddEmailRequest,
    _check_email_code,
    _email_code_confirms,
    _email_code_unavailable,
    _own_email,
    _send_email_code,
)
from app.core.client_address import resolved_client_address
from app.domain.passkey.models import PasskeyCredential
from app.domain.user.login_security import TOTPService
from app.domain.user.models import User
from app.domain.user.verification_service import (
    EmailCodePurpose,
    EmailVerificationService,
)

router = APIRouter(prefix="/users", tags=["Users"])


async def get_user_profile_service(
    db=Depends(get_db),
) -> UserProfileService:
    profile_repo = UserProfileRepository(session=db)
    return UserProfileService(profile_repo=profile_repo)


def _email_taken() -> ConflictError:
    return ConflictError("Email already registered", {"reason": "email_taken"})


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
