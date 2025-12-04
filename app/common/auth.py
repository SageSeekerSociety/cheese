from __future__ import annotations

from typing import Annotated

from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Header

from app.core.config import settings
from app.core.errors import AuthenticationRequiredError


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(user_id: int) -> str:
    """Create a short-lived access token for the given user."""
    now = _utcnow()
    payload = {
        "sub": str(user_id),
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int(
            (now + timedelta(seconds=settings.access_token_expires_seconds)).timestamp()
        ),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def create_refresh_token(user_id: int) -> str:
    """Create a longer-lived refresh token for the given user."""
    now = _utcnow()
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int(
            (now + timedelta(seconds=settings.refresh_token_expires_seconds)).timestamp()
        ),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict:
    """Decode and verify a JWT, returning its payload."""
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:  # type: ignore[attr-defined]
        raise AuthenticationRequiredError("Invalid or expired token") from exc


async def get_current_user_id(
    x_user_id: Annotated[int | None, Header(alias="X-User-Id", convert_underscores=False)] = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> int:
    """Resolve current user ID from Authorization bearer token or X-User-Id header.

    - Preferred: `Authorization: Bearer <accessToken>` issued by Python auth flow.
    - Fallback: `X-User-Id` header (used in tests / transitional environments).
    """
    if authorization:
        token = authorization.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        payload = decode_token(token)
        sub = payload.get("sub")
        try:
            return int(sub)
        except (TypeError, ValueError) as exc:
            raise AuthenticationRequiredError("Invalid token subject") from exc

    if x_user_id is not None:
        return x_user_id

    raise AuthenticationRequiredError("Authorization header or X-User-Id is required")


async def get_optional_user_id(
    x_user_id: Annotated[int | None, Header(alias="X-User-Id", convert_underscores=False)] = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> int | None:
    """Best-effort variant of get_current_user_id that returns None instead of 401.

    用于那些「可选」依赖当前用户上下文的查询（例如 task 列表的 joined 过滤），
    不强制要求调用方携带认证信息。
    """
    from app.core.errors import BaseError

    try:
        return await get_current_user_id(x_user_id=x_user_id, authorization=authorization)
    except (AuthenticationRequiredError, BaseError):
        return None
