"""The password routes: get a forgotten one back, or replace a known one.

Second slice of `app/api/routes/users.py` (arch review C-backend.md §3.3).
users.py is 4,646 lines against a 1,500-line cap that only ratchets down. The
block that moves is the one concept "the account's password": the two halves of
recovery (`POST /users/recover/password/request` and `/verify`) and the change
itself (`PATCH /users/{userId}/password`), each with the request body it reads.

What is shared, and where it lives. `require_new_password` is used by these
routes but not only by them — it also refuses the password a registration
or a two-factor disable asks for — so it lives in
`app/domain/user/passwords.py` with the bcrypt length limit it applies, the
one home every password rule has. `get_user_auth_service` is a FastAPI
dependency in `app/api/deps.py`.
`_spend_sudo_ticket` is imported from `users_common.py` rather than from
`users.py`: the fourth slice of this split moved the passkey, two-factor and
identity routes out, and a helper that many groups redeem a ticket through now
lives with the reservation scope it claims in.

The three request models move with their routes: nothing else in the tree names
`ForgotPasswordRequest`, `ResetPasswordRequest` or `ChangePasswordRequest`, so
they are this module's own shapes now rather than three more classes at the top
of the file being split.

Ordering. This module sorts after `users.py` (`.` < `_`), so its router mounts
after that file's. The paths it owns collide with nothing registered earlier:
`POST /users/{a}/{b}/{c}` does not exist anywhere, so `/users/recover/password/*`
has no parameterized route that could shadow it, and `PATCH /users/{userId}/password`
is the only PATCH of that shape in the tree. The relative order of the three
routes is preserved.

The new module mounts itself: app.main._discover_routers includes every
module-level APIRouter under app.api.routes, so the same
APIRouter(prefix="/users", tags=["Users"]) is all it takes.
"""

import logging
import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis as AsyncRedis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_user_auth_service
from app.api.routes.users_common import _spend_sudo_ticket
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.common.auth import SudoPurpose, get_current_session_id
from app.core import email as core_email
from app.core.background import spawn
from app.core.client_address import resolved_client_address
from app.core.config import settings
from app.core.errors import ForbiddenError, UnprocessableEntityError
from app.db.session import get_db
from app.domain.user.login_security import PasswordResetService
from app.domain.user.mail_quota import MailQuota, give_back_site_mail, take_site_mail
from app.domain.user.passwords import require_new_password
from app.domain.user.services import UserAuthService
from app.domain.user.sessions import RevokeReason, SessionService
from app.domain.user.trusted_devices import TrustedDeviceService

logger = logging.getLogger(__name__)


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    password: str
    sudo_ticket: str | None = Field(default=None, alias="sudoTicket")


class ForgotPasswordRequest(BaseModel):
    email: str = Field(..., min_length=1)


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    token: str = Field(..., min_length=1)
    password: str


router = APIRouter(prefix="/users", tags=["Users"])

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
    sent = await core_email.get_email_sender().send(
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
    token = payload.token
    new_password = payload.password
    require_new_password(new_password)

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
    require_new_password(password)

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
