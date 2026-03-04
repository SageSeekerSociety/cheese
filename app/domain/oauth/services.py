from __future__ import annotations

import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import settings
from app.core.errors import BadRequestError, NotFoundError
from app.domain.oauth.repositories import OAuthConnectionRepository


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
                username=data.get("email", "").split("@")[0] if data.get("email") else None,
            )


PROVIDER_CLASSES = {
    "github": GitHubProvider,
    "google": GoogleProvider,
}


class OAuthService:
    def __init__(self, repo: OAuthConnectionRepository, redis: Any | None = None) -> None:
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

        return None

    def get_providers_config(self) -> list[dict]:
        self._initialize()
        return [
            {
                "id": p.config.id,
                "name": p.config.name,
                "scope": p.config.scope,
            }
            for p in self._providers.values()
        ]

    def get_provider(self, provider_id: str) -> OAuthProvider:
        self._initialize()
        provider = self._providers.get(provider_id)
        if not provider:
            raise NotFoundError(f"OAuth provider '{provider_id}' not found")
        return provider

    def generate_authorization_url(self, provider_id: str, state: str | None = None) -> str:
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
        refresh_token: str | None = None,
        token_expires: datetime | None = None,
    ) -> dict:
        conn = await self._repo.create(
            user_id=user_id,
            provider_id=provider_id,
            provider_user_id=provider_user_id,
            raw_profile=raw_profile,
            refresh_token=refresh_token,
            token_expires=token_expires,
        )
        return self._connection_to_dict(conn)

    async def list_user_connections(self, user_id: int) -> list[dict]:
        conns = await self._repo.list_by_user(user_id)
        provider_names = {
            "github": "GitHub",
            "google": "Google",
        }
        return [
            {
                "id": c.id,
                "providerId": c.provider_id,
                "providerName": provider_names.get(c.provider_id, c.provider_id),
                "providerUserId": c.provider_user_id,
                "connectedAt": c.created_at.isoformat() if c.created_at else None,
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
