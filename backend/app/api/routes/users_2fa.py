"""Two-step verification: turning it on, off, and reading where it stands.

Fourth slice of `app/api/routes/users.py` (arch review C-backend.md §3.3).
users.py is 4,282 lines against a 1,500-line cap that only ratchets down. The
block that moves is the one concept "the account's second factor", as four
routes:

  POST /users/{userId}/2fa/enable         (offer a secret, or confirm one)
  POST /users/{userId}/2fa/disable
  GET  /users/{userId}/2fa/status
  POST /users/{userId}/2fa/backup-codes   (regenerate)

and the two helpers only they call: `_qr_data_uri` (the reference contract
hands the client a ready-to-render <img src>, not a secret to draw) and
`_notify_2fa_disabled` (the out-of-band mail that turns a silent lockout into
something the owner can still catch). Both are this module's now, and neither
is named anywhere else in the tree.

What stays behind, and why. `_spend_sudo_ticket` is not this group's: it is
what every sudo-gated route in the tree redeems a ticket through, so it lives
in `users_common.py` with the reservation scope it claims in, and this module
imports it together with the shared SudoTicketRequest. `GET /users/{userId}/2fa/status`
reads whether the account has a passkey through `PasskeyService`, using the same
request session as the second-factor status query.
`_issue_2fa_pending_token`, `_spend_2fa_attempt` and the `_PENDING_2FA_SCOPE`
reservation are the *sign-in* half of the second factor, and every entrance
that uses them (`POST /users/auth/verify-2fa`, the emailed-code sign-in, the
login responses) stays in `users.py`.

Ordering. This module sorts after `users.py` (`.` < `_`) and before
`users_identity.py`, `users_passkey.py`, `users_password.py` and
`users_team.py`, so its paths mount after every path that stays. A moved path
is shadowed only if a route registered earlier matches it with a parameter
where it carries a literal; all four carry `2fa` in the segment after
`{userId}`, and no route in the table — before or after this move — puts a
parameter there. Resolving every path in the table confirms each of the four
still reaches the handler it did before, now under
`app.api.routes.users_2fa`. OpenAPI is byte-identical apart from the four
paths' position in the paths object.

The new module mounts itself: `app.main._discover_routers` includes every
module-level `APIRouter` under `app.api.routes`, so the declaration below, with
`APIRouter(prefix="/users", tags=["Users"])`, is all it takes.
"""

import html
import logging
from typing import Annotated

import segno
from fastapi import APIRouter, Body, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_user_auth_service
from app.api.routes.users_common import SudoTicketRequest, _spend_sudo_ticket
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.common.auth import SudoPurpose
from app.core.config import settings
from app.core.email import get_email_sender
from app.core.errors import (
    BadRequestError,
    ForbiddenError,
    UnprocessableEntityError,
)
from app.db.session import get_db
from app.domain.passkey.services import PasskeyService
from app.domain.user.login_security import TOTPService
from app.domain.user.services import UserAuthService
from app.domain.user.trusted_devices import TrustedDeviceService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["Users"])


def _qr_data_uri(payload: str) -> str:
    """PNG data URI of a QR code (reference contract returns a ready-to-render
    <img src> value alongside the otpauth URL)."""

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

    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can view 2FA status.")

    totp_service = TOTPService(session)
    enabled = await totp_service.is_2fa_enabled(user_id)
    has_passkey = await PasskeyService.for_session(session).has_passkey(user_id)

    return {
        "code": 200,
        "message": "Get 2FA status successfully",
        "data": {
            "enabled": enabled,
            "has_passkey": has_passkey,
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
