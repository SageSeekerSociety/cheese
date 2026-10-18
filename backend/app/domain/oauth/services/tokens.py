"""token 这一块：落库前的加解封、GitHub 用户令牌的过期与刷新。

`services.py` 拆开之前是 897 行，这一份是其中的 token 那一块，现在住在
`app/domain/oauth/services/` 里，和门面（包的 `__init__.py`）同一个包。拆开的理由只有
文件体积，不是分层：函数体与类上的方法逐字对应（接收者由 `self` 换成 `svc`），
`app.domain.oauth.services` 照旧再导出 `seal_oauth_token` / `open_oauth_token`。

两条判据跟着搬过来，它们是这里最容易改坏的东西：

- 同一条连接的两个刷新者，本进程内只发一次请求：第二个在 `_refresh_locks` 上排队，
  排队时不占数据库连接 —— 行锁跨着网络调用会把连接池和 dev 一起拖下去。
- 解不开的密文降级成 None，但必须出声：`_decrypt_stored_token` 的每个例外都记日志，
  `test_oauth_service.py` 盯着这条日志。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import Purpose, decrypt, encrypt
from app.core.errors import NotFoundError
from app.domain.oauth.repositories import OAuthConnectionRepository

from .providers import GitHubProvider

if TYPE_CHECKING:
    from app.domain.oauth import services as pkg


logger = logging.getLogger(__name__)


def seal_oauth_token(value: str, *, user_id: int, provider_id: str, field: str) -> str:
    """Encrypt one token column, bound to its owner, provider and column."""
    return encrypt(
        Purpose.OAUTH_TOKEN,
        value,
        bound_to=f"user:{user_id}:{provider_id}:{field}",
    )


def open_oauth_token(stored: str, *, user_id: int, provider_id: str, field: str) -> str:
    return decrypt(
        Purpose.OAUTH_TOKEN,
        stored,
        bound_to=f"user:{user_id}:{provider_id}:{field}",
    )


# Refresh an expiring GitHub user-to-server token this long before it
# actually expires, so a token handed to a caller has headroom to be used.
_GITHUB_TOKEN_REFRESH_MARGIN = timedelta(minutes=5)
# One GitHub refresh per connection per process: the second racer waits here,
# holding no database connection, and reuses the first one's result. This dict
# only ever holds one lock per connection that has refreshed, so it stays small.
_refresh_locks: dict[int, asyncio.Lock] = {}


def _decrypt_stored_token(
    svc: pkg.OAuthService, stored: str, *, user_id: int, provider_id: str, field: str
) -> str | None:
    """A token column decrypted, or None when the ciphertext can't be read.

    Tokens are encrypted at rest (``app.core.crypto``), so a value sealed
    under a key no longer configured makes decryption raise. That must not blow up
    the caller (the repository-connect route treats None as "link the account
    first", it does not handle exceptions from here) — but it must never be
    silent either: an unnoticed token problem is exactly how the "returns raw
    ciphertext" bug survived, so this always logs.
    """
    try:
        return open_oauth_token(
            stored, user_id=user_id, provider_id=provider_id, field=field
        )
    except Exception:
        logger.exception(
            "github sign-in: stored token for user %s could not be "
            "decrypted (was its key removed from DATA_ENCRYPTION_KEY?) — "
            "treating the GitHub connection as unavailable",
            user_id,
        )
        return None


async def get_github_user_token(
    svc: pkg.OAuthService, user_id: int, *, provider_id: str = "github_app"
) -> str | None:
    """The decrypted, usable GitHub App user access token for ``user_id``,
    or None if there's no connected
    account, no stored token, the stored token can't be decrypted, or the
    token is expired with nothing to refresh it with. Callers must treat
    None as "fall back", not raise.
    """
    conn = await svc._repo.get_by_user_and_provider(user_id, provider_id)
    if conn is None or not conn.access_token:
        return None

    if (
        conn.token_expires is None
        or conn.token_expires - datetime.now(UTC) > _GITHUB_TOKEN_REFRESH_MARGIN
    ):
        return svc._decrypt_stored_token(
            conn.access_token,
            user_id=user_id,
            provider_id=conn.provider_id,
            field="access_token",
        )

    if not conn.refresh_token:
        return None

    # The refresh itself commits in its OWN, independent transaction —
    # see _refresh_and_persist_token — so this connection's outer
    # session (whatever the caller is mid-way through) never determines
    # whether a successful GitHub refresh actually survives.
    return await svc._refresh_and_persist_token(conn.id, provider_id)


async def _refresh_and_persist_token(
    svc: pkg.OAuthService, connection_id: int, provider_id: str
) -> str | None:
    """Refresh an expiring GitHub token and commit the result in its OWN
    transaction, on fresh sessions — deliberately NOT reusing whatever
    session/transaction the caller (e.g. the repository-connect route) happens
    to be mid-way through, so a later rollback there cannot lose a token
    GitHub has already rotated (决策 0556ac50).

    GitHub App refresh tokens are one-shot, so two refreshers of the same
    connection must not both spend the stored one, and the loser must not
    clobber the winner's write. No database lock is held while GitHub is
    called: a row lock held across a network call queues every later
    caller behind it with a pool connection each, which is how dev went
    down on 2026-09-18. Instead:

    - refreshers of one connection in this process take an asyncio lock,
      so the second one waits (holding nothing) and then reads the fresh
      token the first one committed;
    - the write is conditional on the stored refresh_token still being the
      ciphertext this refresher read, so a refresher in another process
      that lands first keeps its result and this one reads it back.

    The new sessions are opened on the SAME engine as this service's own
    (``svc._repo.session``), not a hardcoded module-level factory: the
    caller's session is the source of truth for which database is live
    (the test harness in particular binds the app's default session
    factory and a given request's session to different databases).

    The provider lookup is deliberately done FIRST: it depends only on
    ``provider_id`` (static, known up front), so failing fast here avoids
    the lock and the reads for a refresh that can never succeed on this
    deployment.
    """
    try:
        provider = svc.get_provider(provider_id)
    except NotFoundError:
        # Provider not configured on this deployment
        # (oauth_enabled_providers / client id + secret unset).
        # Degrade like any other missing prerequisite — raising here
        # would escape into the caller.
        logger.warning(
            "github sign-in: provider %r is not configured, "
            "cannot refresh an expiring token",
            provider_id,
        )
        return None
    if not isinstance(provider, GitHubProvider):
        return None

    bind = svc._repo.session.bind
    lock = _refresh_locks.setdefault(connection_id, asyncio.Lock())
    async with lock:
        async with AsyncSession(bind=bind, expire_on_commit=False) as session:
            conn = await OAuthConnectionRepository(session).get(connection_id)
            if conn is None:
                return None
            if (
                conn.token_expires is not None
                and conn.token_expires - datetime.now(UTC)
                > _GITHUB_TOKEN_REFRESH_MARGIN
            ):
                # Someone else refreshed it while we waited for the lock.
                return (
                    svc._decrypt_stored_token(
                        conn.access_token,
                        user_id=conn.user_id,
                        provider_id=conn.provider_id,
                        field="access_token",
                    )
                    if conn.access_token
                    else None
                )
            seen_refresh = conn.refresh_token
            user_id, owner_provider = conn.user_id, conn.provider_id
        stored_refresh = (
            svc._decrypt_stored_token(
                seen_refresh,
                user_id=user_id,
                provider_id=owner_provider,
                field="refresh_token",
            )
            if seen_refresh
            else None
        )
        if not seen_refresh or not stored_refresh:
            return None

        try:
            token_data = await provider.refresh_access_token(stored_refresh)
        except Exception:
            logger.exception("github sign-in: token refresh failed")
            return await svc._current_token_if_fresh(bind, connection_id)
        if token_data.get("error") == "bad_refresh_token":
            # GitHub will never take this refresh token again (expired,
            # revoked, or spent by a refresher elsewhere). Forget it so the
            # settings page stops reporting a renewal token — unless that
            # other refresher has already stored its replacement.
            logger.warning(
                "github sign-in: GitHub rejected the stored refresh token "
                "of connection %s",
                connection_id,
            )
            async with AsyncSession(bind=bind, expire_on_commit=False) as session:
                await OAuthConnectionRepository(
                    session
                ).forget_refresh_token_if_unchanged(
                    connection_id, seen_refresh_token=seen_refresh
                )
                await session.commit()
            return await svc._current_token_if_fresh(bind, connection_id)
        new_access_token = token_data.get("access_token")
        if not new_access_token:
            # A refresher elsewhere may have spent this refresh token first;
            # then its result is the live one.
            logger.error(
                "github sign-in: token refresh answered without a token: %s",
                token_data.get("error") or "no error code",
            )
            return await svc._current_token_if_fresh(bind, connection_id)

        expires_in = token_data.get("expires_in")
        new_expires = (
            datetime.now(UTC) + timedelta(seconds=expires_in) if expires_in else None
        )
        new_refresh_token = token_data.get("refresh_token")
        async with AsyncSession(bind=bind, expire_on_commit=False) as session:
            replaced = await OAuthConnectionRepository(
                session
            ).replace_tokens_if_unchanged(
                connection_id,
                seen_refresh_token=seen_refresh,
                access_token=seal_oauth_token(
                    new_access_token,
                    user_id=user_id,
                    provider_id=owner_provider,
                    field="access_token",
                ),
                refresh_token=seal_oauth_token(
                    new_refresh_token or stored_refresh,
                    user_id=user_id,
                    provider_id=owner_provider,
                    field="refresh_token",
                ),
                token_expires=new_expires,
            )
            await session.commit()
        if replaced:
            return new_access_token
        return await svc._current_token_if_fresh(bind, connection_id)


async def _current_token_if_fresh(
    svc: pkg.OAuthService, bind, connection_id: int
) -> str | None:
    """The stored access token, if another refresher has just renewed it."""
    async with AsyncSession(bind=bind, expire_on_commit=False) as session:
        conn = await OAuthConnectionRepository(session).get(connection_id)
    if (
        conn is None
        or not conn.access_token
        or conn.token_expires is None
        or conn.token_expires - datetime.now(UTC) <= _GITHUB_TOKEN_REFRESH_MARGIN
    ):
        return None
    return svc._decrypt_stored_token(
        conn.access_token,
        user_id=conn.user_id,
        provider_id=conn.provider_id,
        field="access_token",
    )
