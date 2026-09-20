"""Project OAuth access tokens whose expiry is enforced by Forgejo itself."""

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import httpx
from sqlalchemy import delete, select

from app.core.crypto import decrypt_text, encrypt_text
from app.core.db import SessionFactory, async_session_factory
from app.domain.project.models import ForgeToken, ProjectForge

# Forgejo's built-in public client for git-credential-oauth accepts loopback URLs.
CLIENT_ID = "a4792ccc-144e-407e-86c9-5e7d8d9c3269"
REDIRECT_URI = "http://127.0.0.1:9/"


class ForgejoTokenError(RuntimeError):
    pass


class _Form(HTMLParser):
    def __init__(self, html: str):
        super().__init__()
        self.inputs: dict[str, str] = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        name = attributes.get("name")
        if tag == "input" and isinstance(name, str) and name:
            self.inputs[name] = attributes.get("value") or ""


def _require(response: httpx.Response, step: str, *statuses: int) -> None:
    if response.status_code not in statuses:
        raise ForgejoTokenError(
            f"Forgejo OAuth {step} failed (HTTP {response.status_code})"
        )


class ForgejoTokens:
    def __init__(
        self,
        binding: ProjectForge,
        *,
        sessions: SessionFactory = async_session_factory,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.binding = binding
        self.sessions = sessions
        self.transport = transport

    async def _authorize(self) -> tuple[str, datetime]:
        if not self.binding.account_password:
            raise ForgejoTokenError("Forgejo project account is not configured")
        base = self.binding.api_url.removesuffix("/api/v1").rstrip("/")
        verifier = secrets.token_urlsafe(48)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        state = secrets.token_urlsafe(32)
        async with httpx.AsyncClient(
            transport=self.transport, timeout=20, follow_redirects=False
        ) as client:
            response = await client.get(base + "/user/login")
            _require(response, "login form", 200)
            form = _Form(response.text).inputs
            form.update(
                user_name=self.binding.repo.split("/", 1)[0],
                password=decrypt_text(self.binding.account_password),
            )
            response = await client.post(base + "/user/login", data=form)
            _require(response, "login", 302, 303)
            # The configured internal endpoint may strip the public /forge prefix
            # and terminate TLS elsewhere. Send its session only to this same
            # endpoint; redirects are never followed by this private client.
            client.headers["Cookie"] = "; ".join(
                f"{cookie.name}={cookie.value}" for cookie in client.cookies.jar
            )
            response = await client.get(
                base + "/login/oauth/authorize",
                params={
                    "client_id": CLIENT_ID,
                    "redirect_uri": REDIRECT_URI,
                    "response_type": "code",
                    "state": state,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                },
            )
            _require(response, "authorization", 200, 302, 303)
            if response.status_code == 200:
                form = _Form(response.text).inputs
                form["granted"] = "true"
                response = await client.post(base + "/login/oauth/grant", data=form)
                _require(response, "consent", 302, 303)
            # Read the redirect locally; no callback listener or request is needed.
            callback = urlsplit(response.headers.get("location", ""))
            expected = urlsplit(REDIRECT_URI)
            query = parse_qs(callback.query)
            code = query.get("code", [""])[0]
            if (
                (callback.scheme, callback.netloc, callback.path)
                != (expected.scheme, expected.netloc, expected.path)
                or not hmac.compare_digest(query.get("state", [""])[0], state)
                or not code
            ):
                raise ForgejoTokenError("Forgejo OAuth returned an invalid callback")
            issued_at = datetime.now(UTC)
            response = await client.post(
                base + "/login/oauth/access_token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": CLIENT_ID,
                    "code": code,
                    "redirect_uri": REDIRECT_URI,
                    "code_verifier": verifier,
                },
            )
            _require(response, "token exchange", 200)
            data = response.json()
            if (
                not isinstance(data.get("access_token"), str)
                or type(data.get("expires_in")) is not int
                or data["expires_in"] <= 0
            ):
                raise ForgejoTokenError("Forgejo OAuth returned an invalid token")
            # Refresh tokens stay unused; each mint authenticates the project account.
            return data["access_token"], issued_at + timedelta(
                seconds=data["expires_in"]
            )

    async def installation_token(self) -> tuple[str, str]:
        async with self.sessions() as session:
            cached = await session.scalar(
                select(ForgeToken)
                .where(
                    ForgeToken.project_id == self.binding.project_id,
                    ForgeToken.api_url == self.binding.api_url,
                    ForgeToken.username == self.binding.repo.split("/", 1)[0],
                    ForgeToken.expires_at > datetime.now(UTC) + timedelta(minutes=5),
                )
                .order_by(ForgeToken.expires_at.desc())
                .limit(1)
            )
            if cached is not None:
                return decrypt_text(cached.value), cached.expires_at.isoformat()
            await session.rollback()
            token, expires_at = await self._authorize()
            session.add(
                ForgeToken(
                    project_id=self.binding.project_id,
                    api_url=self.binding.api_url,
                    username=self.binding.repo.split("/", 1)[0],
                    value=encrypt_text(token),
                    expires_at=expires_at,
                )
            )
            await session.commit()
            return token, expires_at.isoformat()

    async def write_token(self) -> tuple[str, str]:
        return await self.installation_token()

    async def granted_permissions(self) -> dict[str, str]:
        return {"project account": "owner"}


async def purge_expired_tokens(sessions: SessionFactory) -> dict[str, int]:
    """Remove expired cache entries; this job does not control upstream validity."""
    async with sessions() as session:
        ids = list(
            await session.scalars(
                delete(ForgeToken)
                .where(ForgeToken.expires_at <= datetime.now(UTC))
                .returning(ForgeToken.id)
            )
        )
        await session.commit()
    return {"deleted": len(ids)}
