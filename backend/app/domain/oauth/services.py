import asyncio
import logging
import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt_text, encrypt_text
from app.core.errors import BadRequestError, NotFoundError
from app.domain.oauth.repositories import OAuthConnectionRepository

logger = logging.getLogger(__name__)

# Refresh an expiring GitHub user-to-server token this long before it
# actually expires, so a token handed to a caller has headroom to be used.
_GITHUB_TOKEN_REFRESH_MARGIN = timedelta(minutes=5)
# One GitHub refresh per connection per process: the second racer waits here,
# holding no database connection, and reuses the first one's result. This dict
# only ever holds one lock per connection that has refreshed, so it stays small.
_refresh_locks: dict[int, asyncio.Lock] = {}

# Machine-readable reasons `get_github_user_token_with_reason` /
# `get_github_user_token_for_handle_with_reason` report alongside a None
# token — for callers (两阶段采纳) that need to explain a degrade to a human
# rather than silently falling back. Never derived from the token/ciphertext
# itself, so surfacing one can't leak either.
TOKEN_UNAVAILABLE_NOT_CONNECTED = "not_connected"
TOKEN_UNAVAILABLE_UNDECRYPTABLE = "undecryptable"
TOKEN_UNAVAILABLE_EXPIRED_NO_REFRESH = "expired_no_refresh"
TOKEN_UNAVAILABLE_REFRESH_FAILED = "refresh_failed"
TOKEN_UNAVAILABLE_PROVIDER_NOT_CONFIGURED = "provider_not_configured"


@dataclass
class OAuthUserInfo:
    id: str
    email: str | None = None
    name: str | None = None
    username: str | None = None
    preferred_username: str | None = None


@dataclass
class OAuthProviderConfig:
    id: str
    name: str
    client_id: str
    client_secret: str
    authorization_url: str
    token_url: str
    redirect_url: str
    scope: list[str]
    user_info_url: str | None = None


class OAuthProvider(ABC):
    def __init__(self, config: OAuthProviderConfig) -> None:
        self.config = config

    def get_authorization_url(self, state: str | None = None) -> str:
        params = {
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_url,
            "scope": " ".join(self.config.scope),
            "response_type": "code",
        }
        if state:
            params["state"] = state
        return f"{self.config.authorization_url}?{urlencode(params)}"

    @abstractmethod
    async def exchange_code(self, code: str) -> dict[str, Any]:
        pass

    @abstractmethod
    async def get_user_info(self, access_token: str) -> OAuthUserInfo:
        pass


class GitHubProvider(OAuthProvider):
    async def exchange_code(self, code: str) -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.config.token_url,
                data={
                    "client_id": self.config.client_id,
                    "client_secret": self.config.client_secret,
                    "code": code,
                    "redirect_uri": self.config.redirect_url,
                },
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            return resp.json()

    async def refresh_access_token(self, refresh_token: str) -> dict[str, Any]:
        """Exchange a refresh_token for a new access_token.

        Only meaningful for GitHub Apps that opted into "Expire user
        authorization tokens" — same response shape as ``exchange_code``
        (access_token, expires_in, refresh_token, refresh_token_expires_in).
        """
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.config.token_url,
                data={
                    "client_id": self.config.client_id,
                    "client_secret": self.config.client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            return resp.json()

    async def get_user_info(self, access_token: str) -> OAuthUserInfo:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            resp.raise_for_status()
            data = resp.json()

            email = data.get("email")
            if not email:
                email_resp = await client.get(
                    "https://api.github.com/user/emails",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/vnd.github+json",
                    },
                )
                if email_resp.status_code == 200:
                    emails = email_resp.json()
                    primary = next((e for e in emails if e.get("primary")), None)
                    if primary:
                        email = primary.get("email")

            return OAuthUserInfo(
                id=str(data["id"]),
                email=email,
                name=data.get("name"),
                username=data.get("login"),
                preferred_username=data.get("login"),
            )


class GoogleProvider(OAuthProvider):
    def get_authorization_url(self, state: str | None = None) -> str:
        params = {
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_url,
            "scope": " ".join(self.config.scope),
            "response_type": "code",
            "access_type": "offline",
            "prompt": "consent",
        }
        if state:
            params["state"] = state
        return f"{self.config.authorization_url}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.config.token_url,
                data={
                    "client_id": self.config.client_id,
                    "client_secret": self.config.client_secret,
                    "code": code,
                    "redirect_uri": self.config.redirect_url,
                    "grant_type": "authorization_code",
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def get_user_info(self, access_token: str) -> OAuthUserInfo:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            data = resp.json()

            return OAuthUserInfo(
                id=data["id"],
                email=data.get("email"),
                name=data.get("name"),
                username=data.get("email", "").split("@")[0]
                if data.get("email")
                else None,
            )


class RUCProvider(OAuthProvider):
    """微人大 (Renmin University of China) OAuth2 provider.

    Ported from the legacy NestJS plugin (plugins/oauth/ruc.js). Uses the base
    class authorization-URL builder unchanged; only the token exchange and the
    profile-to-OAuthUserInfo mapping are provider-specific.
    """

    async def exchange_code(self, code: str) -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.config.token_url,
                data={
                    "client_id": self.config.client_id,
                    "client_secret": self.config.client_secret,
                    "grant_type": "authorization_code",
                    "code": code,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            return resp.json()

    async def get_user_info(self, access_token: str) -> OAuthUserInfo:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://v.ruc.edu.cn/apis/oauth2/v1/profile",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            profile = resp.json()

            uid = profile.get("uid")
            if not uid:
                raise BadRequestError(
                    "RUC profile response missing unique user id (uid)"
                )

            profiles = profile.get("profiles") or []
            primary = next((p for p in profiles if p.get("isprimary") is True), {})
            student_no = primary.get("stno") or profile.get("name")

            return OAuthUserInfo(
                id=str(uid),
                email=profile.get("email") or None,
                name=profile.get("name"),
                username=student_no,
                preferred_username=student_no,
            )


PROVIDER_CLASSES = {
    "github": GitHubProvider,
    "google": GoogleProvider,
    "ruc": RUCProvider,
    "github_app": GitHubProvider,
}

# `github_app` is the account-LINK provider: its redirect_url is the
# `/users/me/github-account/callback` handler, which only accepts a state the
# platform minted for a signed-in user. Offered as a way to sign in it can never
# produce a session, so the sign-in page must not list it. Both flows share
# OAuthService because they share the same GitHub OAuth dance; only the purpose
# differs (config.py keeps their settings deliberately separate for the same reason).
LINK_ONLY_PROVIDERS = frozenset({"github_app"})


class OAuthService:
    def __init__(
        self, repo: OAuthConnectionRepository, redis: Any | None = None
    ) -> None:
        self._repo = repo
        self._redis = redis
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

    def generate_authorization_url(
        self, provider_id: str, state: str | None = None
    ) -> str:
        provider = self.get_provider(provider_id)
        if not state:
            state = secrets.token_urlsafe(32)
        return provider.get_authorization_url(state)

    async def handle_callback(
        self, provider_id: str, code: str, state: str | None = None
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
        conn = await self._repo.get_by_provider(provider_id, provider_user_id)
        if not conn:
            return None
        return self._connection_to_dict(conn)

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
        conn = await self._repo.create(
            user_id=user_id,
            provider_id=provider_id,
            provider_user_id=provider_user_id,
            raw_profile=raw_profile,
            access_token=encrypt_text(access_token) if access_token else None,
            refresh_token=encrypt_text(refresh_token) if refresh_token else None,
            token_expires=token_expires,
        )
        return self._connection_to_dict(conn)

    async def update_connection_tokens(
        self,
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
        await self._repo.update_tokens(
            connection_id,
            encrypt_text(access_token) if access_token else None,
            encrypt_text(refresh_token) if refresh_token else None,
            token_expires,
            raw_profile=raw_profile,
        )

    def _decrypt_stored_token(self, stored: str, *, user_id: int) -> str | None:
        """A token column decrypted, or None when the ciphertext can't be read.

        Tokens are Fernet-encrypted at rest, so a rotated ``CHEESE_SECRET`` or a
        legacy plaintext row makes ``decrypt_text`` raise. That must not blow up
        the caller (the accept flow degrades on None, it does not handle
        exceptions from here) — but it must never be silent either: an
        unnoticed token problem is exactly how the "returns raw ciphertext" bug
        survived, so this always logs.
        """
        try:
            return decrypt_text(stored)
        except Exception:
            logger.exception(
                "github account link: stored token for user %s could not be "
                "decrypted (key rotated, or a legacy plaintext row?) — "
                "treating the account link as unavailable",
                user_id,
            )
            return None

    async def get_github_user_token(
        self, user_id: int, *, provider_id: str = "github_app"
    ) -> str | None:
        """The decrypted, usable GitHub user-to-server access token (#192
        account link) for ``user_id``, or None if there's no connected
        account, no stored token, the stored token can't be decrypted, or the
        token is expired with nothing to refresh it with. Callers must treat
        None as "fall back", not raise.
        """
        token, _reason = await self.get_github_user_token_with_reason(
            user_id, provider_id=provider_id
        )
        return token

    async def get_github_user_token_with_reason(
        self, user_id: int, *, provider_id: str = "github_app"
    ) -> tuple[str | None, str | None]:
        """Same contract as ``get_github_user_token``, plus a
        ``TOKEN_UNAVAILABLE_*`` reason whenever the token comes back None —
        for callers (两阶段采纳's accept()) that need to explain a degrade to
        a human instead of just falling back silently. ``reason`` is always
        None when ``token`` is not.
        """
        conn = await self._repo.get_by_user_and_provider(user_id, provider_id)
        if conn is None or not conn.access_token:
            return None, TOKEN_UNAVAILABLE_NOT_CONNECTED

        if (
            conn.token_expires is None
            or conn.token_expires - datetime.now(UTC) > _GITHUB_TOKEN_REFRESH_MARGIN
        ):
            token = self._decrypt_stored_token(conn.access_token, user_id=user_id)
            return token, (None if token else TOKEN_UNAVAILABLE_UNDECRYPTABLE)

        if not conn.refresh_token:
            return None, TOKEN_UNAVAILABLE_EXPIRED_NO_REFRESH

        # The refresh itself commits in its OWN, independent transaction —
        # see _refresh_and_persist_token — so this connection's outer
        # session (whatever the caller is mid-way through) never determines
        # whether a successful GitHub refresh actually survives.
        token = await self._refresh_and_persist_token(conn.id, provider_id)
        return token, (None if token else TOKEN_UNAVAILABLE_REFRESH_FAILED)

    async def _refresh_and_persist_token(
        self, connection_id: int, provider_id: str
    ) -> str | None:
        """Refresh an expiring GitHub token and commit the result in its OWN
        transaction, on fresh sessions — deliberately NOT reusing whatever
        session/transaction the caller (e.g. AcceptService.accept()) happens
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
        (``self._repo.session``), not a hardcoded module-level factory: the
        caller's session is the source of truth for which database is live
        (the test harness in particular binds the app's default session
        factory and a given request's session to different databases).

        The provider lookup is deliberately done FIRST: it depends only on
        ``provider_id`` (static, known up front), so failing fast here avoids
        the lock and the reads for a refresh that can never succeed on this
        deployment.
        """
        try:
            provider = self.get_provider(provider_id)
        except NotFoundError:
            # Provider not configured on this deployment
            # (oauth_enabled_providers / client id + secret unset).
            # Degrade like any other missing prerequisite — raising here
            # would escape into the accept flow.
            logger.warning(
                "github account link: provider %r is not configured, "
                "cannot refresh an expiring token",
                provider_id,
            )
            return None
        if not isinstance(provider, GitHubProvider):
            return None

        bind = self._repo.session.bind
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
                        self._decrypt_stored_token(
                            conn.access_token, user_id=conn.user_id
                        )
                        if conn.access_token
                        else None
                    )
                seen_refresh = conn.refresh_token
                user_id = conn.user_id
            stored_refresh = (
                self._decrypt_stored_token(seen_refresh, user_id=user_id)
                if seen_refresh
                else None
            )
            if not seen_refresh or not stored_refresh:
                return None

            try:
                token_data = await provider.refresh_access_token(stored_refresh)
                new_access_token = token_data["access_token"]
            except Exception:
                # A refresher elsewhere may have spent this refresh token first;
                # then its result is the live one.
                logger.exception("github account link: token refresh failed")
                return await self._current_token_if_fresh(bind, connection_id)

            expires_in = token_data.get("expires_in")
            new_expires = (
                datetime.now(UTC) + timedelta(seconds=expires_in)
                if expires_in
                else None
            )
            new_refresh_token = token_data.get("refresh_token")
            async with AsyncSession(bind=bind, expire_on_commit=False) as session:
                replaced = await OAuthConnectionRepository(
                    session
                ).replace_tokens_if_unchanged(
                    connection_id,
                    seen_refresh_token=seen_refresh,
                    access_token=encrypt_text(new_access_token),
                    refresh_token=encrypt_text(new_refresh_token or stored_refresh),
                    token_expires=new_expires,
                )
                await session.commit()
            if replaced:
                return new_access_token
            return await self._current_token_if_fresh(bind, connection_id)

    async def _current_token_if_fresh(self, bind, connection_id: int) -> str | None:
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
        return self._decrypt_stored_token(conn.access_token, user_id=conn.user_id)

    async def list_user_connections(self, user_id: int) -> list[dict]:
        conns = await self._repo.list_by_user(user_id)
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
                "tokenExpires": (
                    c.token_expires.isoformat() if c.token_expires else None
                ),
                "hasRefreshToken": c.refresh_token is not None,
            }
            for c in conns
        ]

    async def delete_connection(self, connection_id: int, user_id: int) -> bool:
        return await self._repo.delete_by_id(connection_id, user_id)

    def _connection_to_dict(self, conn) -> dict:
        return {
            "id": conn.id,
            "userId": conn.user_id,
            "providerId": conn.provider_id,
            "providerUserId": conn.provider_user_id,
            "createdAt": conn.created_at.isoformat() if conn.created_at else None,
        }

    async def store_oauth_state(self, state_token: str, data: dict) -> None:
        import json

        if self._redis is None:
            return
        key = f"oauth_state:{state_token}"
        await self._redis.set(key, json.dumps(data), ex=600)

    async def get_oauth_state(self, state_token: str) -> dict | None:
        import json

        if self._redis is None:
            return None
        key = f"oauth_state:{state_token}"
        raw = await self._redis.get(key)
        if raw is None:
            return None
        await self._redis.delete(key)
        return json.loads(raw)


async def get_github_user_token_for_handle(
    session: Any, handle: str, *, provider_id: str = "github_app"
) -> str | None:
    """批准人已连接的 GitHub 账号 token (两阶段采纳 PR迭代式, 2026-08-09).

    Defaults to the "github_app" connection (#192's user-to-server flow for
    cheesex-app, distinct from the plain "github" login provider) since PR
    open/merge needs to act as the App on the user's behalf.

    A thin handle → user_id adapter over ``OAuthService.get_github_user_token``,
    deliberately NOT its own token lookup: tokens are Fernet-encrypted at rest
    (``create_connection`` / ``update_connection_tokens`` both call
    ``encrypt_text``), and expiry/refresh handling lives on that method. Reading
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
    from app.domain.user.repositories import UserRepository

    user = await UserRepository(session).get_by_handle(handle)
    if user is None:
        return None, TOKEN_UNAVAILABLE_NOT_CONNECTED
    service = OAuthService(OAuthConnectionRepository(session))
    return await service.get_github_user_token_with_reason(
        user.id, provider_id=provider_id
    )
