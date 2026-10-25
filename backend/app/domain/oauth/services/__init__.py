"""OAuth 的门面：注册表、授权回调，以及拆出去那三块的转发与再导出。

这个包在拆开之前是 `services.py` 一个 897 行的模块，一个文件里住着四件事：provider
协议、token 的加解封与过期刷新、连接的读写、按 handle 取 GitHub 身份。前三件事搬进了
**同一个包里的子模块** —— `providers.py`、`tokens.py`、`connections.py`；`__init__.py`
留的是**这次部署启用哪些 provider、凭据从哪读、授权怎么走**，以及末尾三个按 handle
取身份的模块级函数。

拆成**包**而不是三个同级模块，是为了不欠新债：子模块 import 的是父包这个节点，而
「子指向祖先」那条边在 C3（`app.domain` 下的同级包不许成环）上本来就不算环；三个同级
模块互相引用会凭空多出环来。形状和 `app/domain/feedback/repositories/` 一样。

路由（`app/api/routes/users/oauth.py`、
`github_install.py`、`users/connections.py`）、
`app/api/deps.py`、`app/domain/repository/identity.py` 里的函数内导入、以及单测都按
`from app.domain.oauth.services import …` 取名字，这里逐个再导出；`settings` 也仍
从这里读，所以按 `app.domain.oauth.services.settings` 打的补丁照旧生效（读 settings
的代码本来就在本模块）。

**类上的方法只转发。** 搬走的方法在类上留一行，把 `self` 和自己的参数原样交给
`tokens.py` / `connections.py` 里接收者是 `svc` 的同名函数。这样类上不再有任何判断
（一条判据只有一处实现），而且 `patch.object(svc, "_refresh_and_persist_token")`
这类打在实例上的补丁仍然拦得住内部调用 —— 类里互相调用全部走 `svc.<name>(...)`。

`_initialize` / `_get_provider_config` / `get_providers_config` / `get_provider` /
`generate_authorization_url` / `handle_callback` 留在类里不搬：它们读 `settings` 和
这张注册表，管的是 `self._providers` / `self._initialized` 这两个门面自己的字段。
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.core import origins
from app.core.config import browser_origin, settings
from app.core.errors import BadRequestError, NotFoundError
from app.domain.oauth.models import UserOAuthConnection
from app.domain.oauth.repositories import OAuthConnectionRepository
from app.domain.user.models import User

from . import connections, tokens
from .providers import (
    PROVIDER_CLASSES,
    GitHubProvider,
    GoogleProvider,
    OAuthProvider,
    OAuthProviderConfig,
    OAuthUserInfo,
    RUCProvider,
)
from .tokens import (
    open_oauth_token,
    seal_oauth_token,
)


def _sign_in_origin(redirect_url: str) -> str | None:
    origin = browser_origin(redirect_url)
    return origin if origins.is_ours(origin) else None


class OAuthService:
    def __init__(self, repo: OAuthConnectionRepository) -> None:
        self._repo = repo
        self._providers: dict[str, OAuthProvider] = {}
        self._initialized = False

    def _initialize(self) -> None:
        if self._initialized:
            return

        enabled = getattr(settings, "oauth_enabled_providers", "")
        if not enabled:
            self._initialized = True
            return

        provider_ids = [p.strip() for p in enabled.split(",") if p.strip()]

        for provider_id in provider_ids:
            config = self._get_provider_config(provider_id)
            if config and provider_id in PROVIDER_CLASSES:
                self._providers[provider_id] = PROVIDER_CLASSES[provider_id](config)

        self._initialized = True

    def _get_provider_config(self, provider_id: str) -> OAuthProviderConfig | None:
        client_id = getattr(settings, f"oauth_{provider_id}_client_id", None)
        client_secret = getattr(settings, f"oauth_{provider_id}_client_secret", None)
        redirect_url = getattr(settings, f"oauth_{provider_id}_redirect_url", None)

        if not client_id or not client_secret or not redirect_url:
            return None

        if provider_id == "google":
            return OAuthProviderConfig(
                id=provider_id,
                name="Google",
                client_id=client_id,
                client_secret=client_secret,
                authorization_url="https://accounts.google.com/o/oauth2/v2/auth",
                token_url="https://oauth2.googleapis.com/token",
                redirect_url=redirect_url,
                scope=["openid", "email", "profile"],
            )
        elif provider_id == "ruc":
            return OAuthProviderConfig(
                id=provider_id,
                name="微人大",
                client_id=client_id,
                client_secret=client_secret,
                authorization_url="https://v.ruc.edu.cn/oauth2/authorize",
                token_url="https://v.ruc.edu.cn/oauth2/token",
                redirect_url=redirect_url,
                scope=["profile", "userinfo"],
            )
        elif provider_id == "github_app":
            # cheesex-app's user-to-server authorization. A GitHub App takes
            # no scopes: what the token may do is the App's permissions on
            # the repositories it is
            # installed on, intersected with the person's own.
            return OAuthProviderConfig(
                id=provider_id,
                name="GitHub",
                client_id=client_id,
                client_secret=client_secret,
                authorization_url="https://github.com/login/oauth/authorize",
                token_url="https://github.com/login/oauth/access_token",
                redirect_url=redirect_url,
                scope=[],
            )

        return None

    def get_providers_config(self) -> list[dict]:
        """The providers a person can sign in with; the sign-in page renders
        every entry as a button.

        ``origin`` is where a provider's sign-in has to run: the origin of the
        callback it is registered with, when that is one of this deployment's
        (`app.core.origins`). A page on another origin signs in there and has
        the sign-in handed back (`app.api.routes.app_sign_in`)."""
        self._initialize()
        return [
            {
                "id": p.config.id,
                "name": p.config.name,
                "scope": p.config.scope,
                "origin": _sign_in_origin(p.config.redirect_url),
            }
            for p in self._providers.values()
        ]

    def get_provider(self, provider_id: str) -> OAuthProvider:
        self._initialize()
        provider = self._providers.get(provider_id)
        if not provider:
            raise NotFoundError(f"OAuth provider '{provider_id}' not found")
        return provider

    def generate_authorization_url(self, provider_id: str, state: str) -> str:
        return self.get_provider(provider_id).get_authorization_url(state)

    async def handle_callback(
        self, provider_id: str, code: str
    ) -> tuple[str, OAuthUserInfo]:
        provider = self.get_provider(provider_id)

        token_data = await provider.exchange_code(code)
        access_token = token_data.get("access_token")
        if not access_token:
            raise BadRequestError("Failed to get access token")

        user_info = await provider.get_user_info(access_token)
        return access_token, user_info

    async def get_connection_by_provider(
        self, provider_id: str, provider_user_id: str
    ) -> dict | None:
        return await connections.get_connection_by_provider(
            self, provider_id, provider_user_id
        )

    async def create_connection(
        self,
        *,
        user_id: int,
        provider_id: str,
        provider_user_id: str,
        raw_profile: dict | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
        token_expires: datetime | None = None,
        refresh_token_expires: datetime | None = None,
    ) -> dict:
        return await connections.create_connection(
            self,
            user_id=user_id,
            provider_id=provider_id,
            provider_user_id=provider_user_id,
            raw_profile=raw_profile,
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires=token_expires,
            refresh_token_expires=refresh_token_expires,
        )

    async def update_connection_tokens(
        self,
        *,
        connection_id: int,
        access_token: str | None,
        refresh_token: str | None,
        token_expires: datetime | None,
        raw_profile: dict | None = None,
        refresh_token_expires: datetime | None = None,
    ) -> None:
        return await connections.update_connection_tokens(
            self,
            connection_id=connection_id,
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires=token_expires,
            raw_profile=raw_profile,
            refresh_token_expires=refresh_token_expires,
        )

    def _decrypt_stored_token(
        self, stored: str, *, user_id: int, provider_id: str, field: str
    ) -> str | None:
        return tokens._decrypt_stored_token(
            self, stored, user_id=user_id, provider_id=provider_id, field=field
        )

    async def get_github_user_token(
        self, user_id: int, *, provider_id: str = "github_app"
    ) -> str | None:
        return await tokens.get_github_user_token(
            self, user_id, provider_id=provider_id
        )

    async def _refresh_and_persist_token(
        self, connection_id: int, provider_id: str
    ) -> str | None:
        return await tokens._refresh_and_persist_token(self, connection_id, provider_id)

    async def _current_token_if_fresh(self, bind, connection_id: int) -> str | None:
        return await tokens._current_token_if_fresh(self, bind, connection_id)

    async def list_user_connections(self, user_id: int) -> list[dict]:
        return await connections.list_user_connections(self, user_id)

    async def delete_connection(self, connection_id: int, user_id: int) -> bool:
        return await connections.delete_connection(self, connection_id, user_id)

    def _connection_to_dict(self, conn) -> dict:
        return connections._connection_to_dict(conn)


async def get_github_profile_for_handle(
    session: Any, handle: str
) -> tuple[str, dict] | None:
    """This handle's GitHub sign-in as ``(provider_user_id, profile)``.

    The read behind commit/PR attribution (``repository/identity.py``), which
    needs the numeric GitHub id and the login and has no business reaching into
    this domain's repositories for them.
    """
    # deferred-import: tests replace this name on app.domain.user.repositories
    from app.domain.user.repositories import UserRepository

    user = await UserRepository(session).get_by_handle(handle)
    if user is None:
        return None
    conn = await OAuthConnectionRepository(session).get_by_user_and_provider(
        user.id, "github_app"
    )
    if conn is None or not conn.provider_user_id:
        return None
    return conn.provider_user_id, dict(conn.raw_profile or {})


async def handles_for_github_ids(
    session: Any, github_ids: Collection[str | int | None]
) -> dict[str, str]:
    """GitHub 数字 id → 平台 handle，一次查完；没绑定过的 id 不在结果里。

    这是 ``get_github_profile_for_handle`` 的反方向：卡上存的是 GitHub 报的
    原始 id，显示的时候才换成平台名字。这样谁后来才把 GitHub 账号绑到平台，
    旧记录也认得出来（项目总览的「最近进展」）。一次查完，不按人 N+1：一屏
    进展里可能有好几个不同的合并人。

    账号已删掉的不回：那个 id 已经没有能显示的名字，调用方退回 GitHub 登录名。
    """
    ids = {str(i) for i in github_ids if i}
    if not ids:
        return {}
    stmt = (
        select(UserOAuthConnection.provider_user_id, User.username)
        .join(User, User.id == UserOAuthConnection.user_id)
        .where(
            UserOAuthConnection.provider_id == "github_app",
            UserOAuthConnection.provider_user_id.in_(ids),
            User.deleted_at.is_(None),
        )
    )
    return {
        provider_user_id: username
        for provider_user_id, username in (await session.execute(stmt)).all()
    }


# 兼容门面：`app.domain.oauth.services` 仍是路由、`deps.py`、`identity.py` 和单测
# 的导入路径。拆开之前这个模块的名字空间里有什么，这里就还导出什么 ——
# `GitHubProvider`、`seal_oauth_token` 这些取的仍是子模块里
# 那一个，不是副本。
__all__ = [
    "PROVIDER_CLASSES",
    "GitHubProvider",
    "GoogleProvider",
    "OAuthConnectionRepository",
    "OAuthProvider",
    "OAuthProviderConfig",
    "OAuthService",
    "OAuthUserInfo",
    "RUCProvider",
    "get_github_profile_for_handle",
    "handles_for_github_ids",
    "open_oauth_token",
    "seal_oauth_token",
]


async def get_github_user_token_for_handle(session: Any, handle: str) -> str | None:
    """A usable token of this handle's GitHub account, or None. The credential
    a task owner's PR is opened with."""
    # deferred-import: tests replace this name on app.domain.user.repositories
    from app.domain.user.repositories import UserRepository

    user = await UserRepository(session).get_by_handle(handle)
    if user is None:
        return None
    return await OAuthService(OAuthConnectionRepository(session)).get_github_user_token(
        user.id
    )


async def github_sign_in_state(session: Any, handle: str) -> str:
    """Whether this handle's GitHub account can open a PR in its name, read
    from what is stored and never by asking GitHub: ``"none"`` (no GitHub sign-in),
    ``"expired"`` (its token has run out and nothing renews it) or
    ``"ok"``. Cheap enough for a page that polls."""
    # deferred-import: tests replace this name on app.domain.user.repositories
    from app.domain.user.repositories import UserRepository

    user = await UserRepository(session).get_by_handle(handle)
    if user is None:
        return "none"
    conn = await OAuthConnectionRepository(session).get_by_user_and_provider(
        user.id, "github_app"
    )
    if conn is None or not conn.access_token:
        return "none"
    if (
        conn.token_expires is not None
        and conn.token_expires <= datetime.now(UTC)
        and not conn.refresh_token
    ):
        return "expired"
    return "ok"
