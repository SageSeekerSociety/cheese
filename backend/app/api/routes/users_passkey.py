"""Passkeys: registering one, signing in with one, listing and removing them.

Fourth slice of `app/api/routes/users.py` (arch review C-backend.md §3.3).
users.py is 4,282 lines against a 1,500-line cap that only ratchets down. The
block that moves is the one concept "a passkey that belongs to this account",
as seven routes:

  POST   /users/{userId}/passkeys/options         registration options
  POST   /users/{userId}/passkeys                 finish registration
  POST   /users/{userId}/passkeys/prompt/dismiss  decline the offer
  POST   /users/auth/passkey/options              sign-in options
  POST   /users/auth/passkey/verify               finish sign-in
  GET    /users/{userId}/passkeys                 list
  DELETE /users/{userId}/passkeys/{credentialId}  remove one

and the one shape only they use, `PasskeyPromptDismissal`.

Shared contracts. `challenge_from_credential` reads WebAuthn clientDataJSON
for both these verification routes and sudo, so it lives in users_common
beside SudoTicketRequest. `_passkey_enrollment` (what a finished sign-in
hands back when the account is due the offer) stays in users.py:
every sign-in route calls it. `_spend_sudo_ticket` is the shared helper and
lives in `users_common.py`, and `issue_session` joined it there: every way
of signing in ends in it, so the mint sits beside the ticket these modules
already share, imported here the way `app_sign_in.py` imports it.
`get_passkey_service` and `get_user_auth_service` are FastAPI dependencies
assembled in `app/api/deps.py`.

Ordering. This module sorts after `users.py`, `users_2fa.py` and
`users_identity.py`, so its paths mount after every path that stays. The two
`/users/auth/passkey/*` paths are the interesting ones: `users.py` still
registers `/users/auth/*` paths of its own (`/auth/login`, `/auth/sudo`,
`/auth/refresh-token`, `/auth/logout`, `/auth/verify-2fa`,
`/auth/email-code`, `/auth/oauth/*`), and each names a literal where this pair
does, so none of them can match `/users/auth/passkey/options` first. The five
account-scoped paths all carry `passkeys` where no route registered earlier has
a parameter. Resolving every
path in the table confirms each of the seven still reaches the handler it did
before, now under `app.api.routes.users_passkey`. OpenAPI is byte-identical
apart from the seven paths' position in the paths object.

The new module mounts itself: `app.main._discover_routers` includes every
module-level `APIRouter` under `app.api.routes`, so the declaration below, with
`APIRouter(prefix="/users", tags=["Users"])`, is all it takes.
"""

import json
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Request, Response
from pydantic import BaseModel
from redis.asyncio import Redis as AsyncRedis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_passkey_service, get_user_auth_service
from app.api.routes.users_common import (
    SudoTicketRequest,
    _spend_sudo_ticket,
    challenge_from_credential,
    issue_session,
)
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.common.auth import SudoPurpose
from app.core.config import settings
from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.db.session import get_db
from app.domain.passkey.prompt import PasskeyPromptService
from app.domain.passkey.services import PasskeyService
from app.domain.user.services import UserAuthService

router = APIRouter(prefix="/users", tags=["Users"])


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

    if auth_user.user_id != user_id:
        raise ForbiddenError("Only the user themselves can register a passkey.")

    credential = payload.get("response")
    if not isinstance(credential, dict):
        raise BadRequestError("response (WebAuthn credential) is required")
    challenge = challenge_from_credential(credential)

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

    credential = payload.get("response")
    if not isinstance(credential, dict):
        raise BadRequestError("response (WebAuthn credential) is required")
    challenge = challenge_from_credential(credential)

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
