"""连接这一行本身：查、建、改 token、列、删。

`services.py` 拆开之前是 897 行，这一份是其中的连接那一块 —— 账号绑定的状态。它现在
住在 `app/domain/oauth/services/` 里，和门面（包的 `__init__.py`）同一个包；token 怎么
封、什么时候算过期在 `tokens.py`，provider 怎么说话在 `providers.py`。拆开的理由只有
文件体积，不是分层：函数体与类上的方法逐字对应，门面类上的同名方法是一句转发。

两条判据跟着搬过来：

- `create_connection` 撞唯一键时重查一次：查得到才算「已经绑过」
  （`ConflictError`），查不到把 `IntegrityError` 放行 —— 并发下先查再写守不住，
  唯一键才是判据。
- `delete_connection` 不许删掉用户最后一种登录方式：真邮箱、密码、通行密钥和其他
  非 link-only 连接各算一种，`github_app` 这类只用于绑定的不算，所以它既不被保护
  也占不了名额。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.email import is_placeholder_email
from app.core.errors import ConflictError, NotFoundError
from app.core.sentences import say
from app.domain.passkey.services import PasskeyService
from app.domain.user.models import User

from .providers import LINK_ONLY_PROVIDERS
from .tokens import seal_oauth_token

if TYPE_CHECKING:
    from app.domain.oauth import services as pkg


logger = logging.getLogger(__name__)


async def get_connection_by_provider(
    svc: pkg.OAuthService, provider_id: str, provider_user_id: str
) -> dict | None:
    conn = await svc._repo.get_by_provider(provider_id, provider_user_id)
    if not conn:
        return None
    return svc._connection_to_dict(conn)


async def create_connection(
    svc: pkg.OAuthService,
    *,
    user_id: int,
    provider_id: str,
    provider_user_id: str,
    raw_profile: dict | None = None,
    access_token: str | None = None,
    refresh_token: str | None = None,
    token_expires: datetime | None = None,
) -> dict:
    """Raises ``ConflictError`` when this provider identity is already
    linked, whatever a preceding lookup said."""
    try:
        conn = await svc._repo.create(
            user_id=user_id,
            provider_id=provider_id,
            provider_user_id=provider_user_id,
            raw_profile=raw_profile,
            access_token=seal_oauth_token(
                access_token,
                user_id=user_id,
                provider_id=provider_id,
                field="access_token",
            )
            if access_token
            else None,
            refresh_token=seal_oauth_token(
                refresh_token,
                user_id=user_id,
                provider_id=provider_id,
                field="refresh_token",
            )
            if refresh_token
            else None,
            token_expires=token_expires,
        )
    except IntegrityError:
        if await svc._repo.get_by_provider(provider_id, provider_user_id) is None:
            raise
        raise ConflictError(
            "This OAuth account is already linked",
            data={"providerId": provider_id},
        ) from None
    return svc._connection_to_dict(conn)


async def update_connection_tokens(
    svc: pkg.OAuthService,
    *,
    connection_id: int,
    access_token: str | None,
    refresh_token: str | None,
    token_expires: datetime | None,
    raw_profile: dict | None = None,
) -> None:
    """``raw_profile=None`` (the default) leaves the stored profile as is —
    a token refresher has no profile to offer. A flow that DID just fetch
    the provider's user endpoint should pass it, so re-linking repairs a
    profile that was stored incomplete."""
    conn = await svc._repo.get(connection_id)
    if conn is None:
        raise NotFoundError("OAuth connection not found")
    user_id, provider_id = conn.user_id, conn.provider_id
    await svc._repo.update_tokens(
        connection_id,
        seal_oauth_token(
            access_token,
            user_id=user_id,
            provider_id=provider_id,
            field="access_token",
        )
        if access_token
        else None,
        seal_oauth_token(
            refresh_token,
            user_id=user_id,
            provider_id=provider_id,
            field="refresh_token",
        )
        if refresh_token
        else None,
        token_expires,
        raw_profile=raw_profile,
    )


async def list_user_connections(svc: pkg.OAuthService, user_id: int) -> list[dict]:
    conns = await svc._repo.list_by_user(user_id)
    provider_names = {
        "github": "GitHub",
        "google": "Google",
        "ruc": "微人大",
        "github_app": "GitHub (cheesex-app)",
    }
    return [
        {
            "id": c.id,
            "providerId": c.provider_id,
            "providerName": provider_names.get(c.provider_id, c.provider_id),
            "providerUserId": c.provider_user_id,
            "connectedAt": c.created_at.isoformat() if c.created_at else None,
            # Token 健康度 (2026-08-09): 只暴露元数据，绝不返回 token/密文本身.
            "login": (c.raw_profile or {}).get("login"),
            "tokenExpires": (c.token_expires.isoformat() if c.token_expires else None),
            "hasRefreshToken": c.refresh_token is not None,
        }
        for c in conns
    ]


async def delete_connection(
    svc: pkg.OAuthService, connection_id: int, user_id: int
) -> bool:
    """Remove a connection unless it is the user's last way to sign in.

    A real email (codes are mailed to it), a password, a passkey and every
    other sign-in connection each count as a way in; link-only connections
    never produce a session, so they neither count nor are ever protected.
    Raises ``ConflictError`` when refused.
    """

    conn = await svc._repo.get(connection_id)
    if conn is None or conn.user_id != user_id:
        return False
    if conn.provider_id not in LINK_ONLY_PROVIDERS:
        session = svc._repo.session
        # Row lock: two concurrent unbinds must not each see the other's
        # connection as the one that remains.
        user = (
            await session.execute(
                select(User).where(User.id == user_id).with_for_update()
            )
        ).scalar_one()
        other_sign_in = any(
            c.id != connection_id and c.provider_id not in LINK_ONLY_PROVIDERS
            for c in await svc._repo.list_by_user(user_id)
        )
        has_passkey = await PasskeyService.for_session(session).has_passkey(user_id)
        if not (
            user.hashed_password
            or has_passkey
            or other_sign_in
            or not is_placeholder_email(user.email)
        ):
            raise ConflictError(say("oauthLastSignInMethod"))
    return await svc._repo.delete_by_id(connection_id, user_id)


def _connection_to_dict(conn) -> dict:
    return {
        "id": conn.id,
        "userId": conn.user_id,
        "providerId": conn.provider_id,
        "providerUserId": conn.provider_user_id,
        "createdAt": conn.created_at.isoformat() if conn.created_at else None,
    }
