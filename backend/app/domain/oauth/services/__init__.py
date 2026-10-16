"""OAuth 的门面：注册表、授权回调，以及拆出去那三块的转发与再导出。

这个包在拆开之前是 `services.py` 一个 897 行的模块，一个文件里住着四件事：provider
协议、token 的加解封与过期刷新、连接的读写、按 handle 取 GitHub 身份。前三件事搬进了
**同一个包里的子模块** —— `providers.py`、`tokens.py`、`connections.py`；`__init__.py`
留的是**这次部署启用哪些 provider、凭据从哪读、授权怎么走**，以及末尾三个按 handle
取身份的模块级函数。

拆成**包**而不是三个同级模块，是为了不欠新债：子模块 import 的是父包这个节点，而
「子指向祖先」那条边在 C3（`app.domain` 下的同级包不许成环）上本来就不算环；三个同级
模块互相引用会凭空多出环来。形状和 `app/domain/feedback/repositories/` 一样。

**这一面的名字和对象一个都没变。** 路由（`app/api/routes/users/oauth.py`、
`github_account_link.py`、`github_install.py`、`users/connections.py`）、
`app/api/deps.py`、`app/domain/repository/identity.py` 里的函数内导入、以及单测都按
`from app.domain.oauth.services import …` 取名字，这里逐个再导出；`settings` 也仍
从这里读，所以按 `app.domain.oauth.services.settings` 打的补丁照旧生效（读 settings
的代码本来就在本模块）。`todo` 里那一条——`test_oauth_service.py` 用到的每个名字都
还取得到——有 `test_oauth_service_module_split.py` 钉着。

**类上的方法只转发。** 搬走的方法在类上留一行，把 `self` 和自己的参数原样交给
`tokens.py` / `connections.py` 里接收者是 `svc` 的同名函数。这样类上不再有任何判断
（一条判据只有一处实现），而且 `patch.object(svc, "_refresh_and_persist_token")`
这类打在实例上的补丁仍然拦得住内部调用 —— 类里互相调用全部走 `svc.<name>(...)`。

`_initialize` / `_get_provider_config` / `get_providers_config` / `get_provider` /
`generate_authorization_url` / `handle_callback` 留在类里不搬：它们读 `settings` 和
这张注册表，管的是 `self._providers` / `self._initialized` 这两个门面自己的字段。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.config import settings
from app.core.errors import BadRequestError, NotFoundError
from app.domain.oauth.repositories import OAuthConnectionRepository

from . import connections, tokens
from .providers import (
    LINK_ONLY_PROVIDERS,
    PROVIDER_CLASSES,
    GitHubProvider,
    GoogleProvider,
    OAuthProvider,
    OAuthProviderConfig,
    OAuthUserInfo,
    RUCProvider,
)
from .tokens import (
    TOKEN_UNAVAILABLE_EXPIRED_NO_REFRESH,
    TOKEN_UNAVAILABLE_NOT_CONNECTED,
    TOKEN_UNAVAILABLE_PROVIDER_NOT_CONFIGURED,
    TOKEN_UNAVAILABLE_REFRESH_FAILED,
    TOKEN_UNAVAILABLE_UNDECRYPTABLE,
    open_oauth_token,
    seal_oauth_token,
)


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

        if provider_id == "github":
            return OAuthProviderConfig(
                id=provider_id,
                name="GitHub",
                client_id=client_id,
                client_secret=client_secret,
                authorization_url="https://github.com/login/oauth/authorize",
                token_url="https://github.com/login/oauth/access_token",
                redirect_url=redirect_url,
                scope=["read:user", "user:email"],
            )
        elif provider_id == "google":
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
            # #192: the cheesex-app GitHub App's OWN user-to-server
            # authorization — a separate credential from the "github" login
            # provider above, even though it's the identical OAuth 2.0 dance
            # (GitHub Apps reuse the classic authorize/token endpoints, just
            # keyed to the App's client_id/secret instead of an OAuth App's).
            return OAuthProviderConfig(
                id=provider_id,
                name="GitHub (cheesex-app)",
                client_id=client_id,
                client_secret=client_secret,
                authorization_url="https://github.com/login/oauth/authorize",
                token_url="https://github.com/login/oauth/access_token",
                redirect_url=redirect_url,
                scope=["read:user", "user:email"],
            )

        return None

    def get_providers_config(self) -> list[dict]:
        """The providers a person can SIGN IN with.

        Link-only providers are configured through this same service but are
        never offered here: the sign-in page renders every entry as a button.
        """
        self._initialize()
        return [
            {
                "id": p.config.id,
                "name": p.config.name,
                "scope": p.config.scope,
            }
            for provider_id, p in self._providers.items()
            if provider_id not in LINK_ONLY_PROVIDERS
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
        )

    async def update_connection_tokens(
        self,
        *,
        connection_id: int,
        access_token: str | None,
        refresh_token: str | None,
        token_expires: datetime | None,
        raw_profile: dict | None = None,
    ) -> None:
        return await connections.update_connection_tokens(
            self,
            connection_id=connection_id,
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires=token_expires,
            raw_profile=raw_profile,
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

    async def get_github_user_token_with_reason(
        self, user_id: int, *, provider_id: str = "github_app"
    ) -> tuple[str | None, str | None]:
        return await tokens.get_github_user_token_with_reason(
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


async def get_github_user_token_for_handle(
    session: Any, handle: str, *, provider_id: str = "github_app"
) -> str | None:
    """批准人已连接的 GitHub 账号 token (两阶段采纳 PR迭代式, 2026-08-09).

    Defaults to the "github_app" connection (#192's user-to-server flow for
    cheesex-app, distinct from the plain "github" login provider) since PR
    open/merge needs to act as the App on the user's behalf.

    A thin handle → user_id adapter over ``OAuthService.get_github_user_token``,
    deliberately NOT its own token lookup: tokens are encrypted at rest
    (``create_connection`` / ``update_connection_tokens`` both call
    ``seal_oauth_token``), and expiry/refresh handling lives on that method. Reading
    ``conn.access_token`` straight off the row — as this function used to — hands
    the caller raw ciphertext, which is non-empty and so passes every
    ``if not token`` guard before failing against GitHub with a 401 that looks
    exactly like "no connected account".

    Returns None, never raises, whenever the mechanism is unavailable: unknown
    handle, no connected account, no or undecryptable token, or an expired token
    with no usable refresh. Callers treat None as "degrade to the direct-merge
    accept path".
    """
    token, _reason = await get_github_user_token_for_handle_with_reason(
        session, handle, provider_id=provider_id
    )
    return token


async def get_github_profile_for_handle(
    session: Any,
    handle: str,
    *,
    provider_ids: tuple[str, ...] = ("github_app", "github"),
) -> tuple[str, dict] | None:
    """This handle's connected GitHub account as ``(provider_user_id, profile)``.

    The read behind commit/PR attribution (``workspace/identity.py``), which
    needs the numeric GitHub id and the login and has no business reaching into
    this domain's repositories for them.

    `github_app` is tried first because it is the connection the accept flow
    already pushes and opens PRs with, so preferring it keeps commit authorship
    and PR authorship pointing at the same account; `github` is the plain login
    connection, present for people who signed in with GitHub but never
    authorized the App.
    """
    # deferred-import: tests replace this name on app.domain.user.repositories
    from app.domain.user.repositories import UserRepository

    user = await UserRepository(session).get_by_handle(handle)
    if user is None:
        return None
    repo = OAuthConnectionRepository(session)
    for provider_id in provider_ids:
        conn = await repo.get_by_user_and_provider(user.id, provider_id)
        if conn is not None and conn.provider_user_id:
            return conn.provider_user_id, dict(conn.raw_profile or {})
    return None


async def get_github_user_token_for_handle_with_reason(
    session: Any, handle: str, *, provider_id: str = "github_app"
) -> tuple[str | None, str | None]:
    """Same contract as ``get_github_user_token_for_handle``, plus a
    ``TOKEN_UNAVAILABLE_*`` reason whenever the token comes back None — see
    ``OAuthService.get_github_user_token_with_reason``. An unknown handle
    (no such platform user, as distinct from "user exists but never
    connected GitHub") is bucketed under ``TOKEN_UNAVAILABLE_NOT_CONNECTED``
    too: from an accept-flow caller's point of view both mean "this approver
    has no usable GitHub identity".
    """
    # deferred-import: tests replace this name on app.domain.user.repositories
    from app.domain.user.repositories import UserRepository

    user = await UserRepository(session).get_by_handle(handle)
    if user is None:
        return None, TOKEN_UNAVAILABLE_NOT_CONNECTED
    service = OAuthService(OAuthConnectionRepository(session))
    return await service.get_github_user_token_with_reason(
        user.id, provider_id=provider_id
    )


# 兼容门面：`app.domain.oauth.services` 仍是路由、`deps.py`、`identity.py` 和单测
# 的导入路径。拆开之前这个模块的名字空间里有什么，这里就还导出什么 ——
# `GitHubProvider`、`seal_oauth_token`、`TOKEN_UNAVAILABLE_*` 这些取的仍是子模块里
# 那一个，不是副本。
__all__ = [
    "LINK_ONLY_PROVIDERS",
    "PROVIDER_CLASSES",
    "GitHubProvider",
    "GoogleProvider",
    "OAuthConnectionRepository",
    "OAuthProvider",
    "OAuthProviderConfig",
    "OAuthService",
    "OAuthUserInfo",
    "RUCProvider",
    "TOKEN_UNAVAILABLE_EXPIRED_NO_REFRESH",
    "TOKEN_UNAVAILABLE_NOT_CONNECTED",
    "TOKEN_UNAVAILABLE_PROVIDER_NOT_CONFIGURED",
    "TOKEN_UNAVAILABLE_REFRESH_FAILED",
    "TOKEN_UNAVAILABLE_UNDECRYPTABLE",
    "get_github_profile_for_handle",
    "get_github_user_token_for_handle",
    "get_github_user_token_for_handle_with_reason",
    "open_oauth_token",
    "seal_oauth_token",
]
