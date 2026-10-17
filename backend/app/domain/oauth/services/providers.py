"""OAuth 的 provider：协议、三家实现、id 到实现的注册表。

`services.py` 拆开之前是 897 行，这一份是其中的 provider 那一块，现在住在
`app/domain/oauth/services/` 里，和门面（包的 `__init__.py`）同一个包。拆开的理由只有
文件体积，不是分层：类体与常量逐字搬过来，门面照旧再导出这一组名字，
`from app.domain.oauth.services import GitHubProvider` 取到的仍是同一个对象。

**这里只回答「这个 provider 怎么说话」**：往哪个授权地址跳、拿什么去换 token、
用户信息里哪些字段可信。这次部署启用哪几个 provider、各自的 client_id/secret 从
哪些 settings 读，在 `OAuthService._get_provider_config`（包的 `__init__.py`）—— 前者
不随部署变，后者随。

`PROVIDER_CLASSES` 里 `github_app` 与 `github` 共用 `GitHubProvider` 是有意的：
#192 的 user-to-server 授权和普通登录是同一套 OAuth 2.0 动作，只是凭据与
redirect_url 不同。

`github_app` 又是唯一一个 `LINK_ONLY_PROVIDERS`：它的 redirect_url 只认平台给已
登录用户签发的 state，当作登录入口永远换不出会话，所以登录页不能列它。
`OAuthService.get_providers_config` 与 `connections.delete_connection` 都读这个
名字，判据只有这一份。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.errors import BadRequestError


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

            # `/user`'s `email` is the public profile email, which GitHub does
            # not require to be verified. Only a verified primary address from
            # `/user/emails` is adopted; anything else counts as no email.
            email = None
            email_resp = await client.get(
                "https://api.github.com/user/emails",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            if email_resp.status_code == 200:
                primary = next(
                    (
                        e
                        for e in email_resp.json()
                        if e.get("primary") and e.get("verified") is True
                    ),
                    None,
                )
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

            # An address Google has not verified counts as no email.
            email = data.get("email") if data.get("verified_email") is True else None
            return OAuthUserInfo(
                id=data["id"],
                email=email,
                name=data.get("name"),
                username=email.split("@")[0] if email else None,
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

            # No verification flag to check: the account and its profile are
            # issued by the university's identity system rather than entered
            # by the user at sign-up, so the email is the institution's record.
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
