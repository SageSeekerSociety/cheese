"""Project OAuth access tokens whose expiry is enforced by Forgejo itself."""

import base64
import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import httpx
from sqlalchemy import delete, select

from app.core.crypto import Purpose, decrypt, encrypt
from app.core.db import SessionFactory, async_session_factory
from app.domain.project.models import ForgeToken, ProjectForge

# Forgejo's built-in public client for git-credential-oauth accepts loopback URLs.
CLIENT_ID = "a4792ccc-144e-407e-86c9-5e7d8d9c3269"
REDIRECT_URI = "http://127.0.0.1:9/"


#: A read-only token (`ForgejoTokens.read_token`): what it may read, how long
#: the platform lets it live, and the name that carries its expiry upstream.
READ_SCOPES = ("read:repository", "read:issue", "read:user")
READ_TOKEN_TTL = timedelta(hours=1)
READ_TOKEN_PREFIX = "cheese-read-"


class ForgejoTokenError(RuntimeError):
    pass


def seal_forge_password(project_id: uuid.UUID, password: str) -> str:
    return encrypt(Purpose.FORGE_PASSWORD, password, bound_to=f"project:{project_id}")


def forge_password(binding: ProjectForge) -> str:
    """The project's forge account password, decrypted."""
    if binding.account_password is None:
        raise ForgejoTokenError("Forgejo project account is not configured")
    return decrypt(
        Purpose.FORGE_PASSWORD,
        binding.account_password,
        bound_to=f"project:{binding.project_id}",
    )


def _token_binding(project_id: uuid.UUID, api_url: str, username: str) -> str:
    return f"project:{project_id}:{api_url}:{username}"


def seal_forge_token(
    project_id: uuid.UUID, api_url: str, username: str, token: str
) -> str:
    return encrypt(
        Purpose.FORGE_TOKEN,
        token,
        bound_to=_token_binding(project_id, api_url, username),
    )


def open_forge_token(token: ForgeToken) -> str:
    return decrypt(
        Purpose.FORGE_TOKEN,
        token.value,
        bound_to=_token_binding(token.project_id, token.api_url, token.username),
    )


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
                password=forge_password(self.binding),
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
        return await self._cached(read_only=False, mint=self._authorize)

    async def read_token(self) -> tuple[str, str]:
        """A token that reads the repository and its issues and writes nothing:
        what ``/sandbox/forge-token`` hands a session whose work is not kept.
        Forgejo grants no scope to the OAuth client the other tokens come from,
        so this is a scoped access token of the project account, which never
        expires on its own: the platform revokes it once it is past
        ``READ_TOKEN_TTL`` (`revoke_expired_read_tokens`)."""
        return await self._cached(read_only=True, mint=self._scoped_token)

    async def _cached(self, *, read_only: bool, mint) -> tuple[str, str]:
        async with self.sessions() as session:
            cached = await session.scalar(
                select(ForgeToken)
                .where(
                    ForgeToken.project_id == self.binding.project_id,
                    ForgeToken.api_url == self.binding.api_url,
                    ForgeToken.username == self.binding.repo.split("/", 1)[0],
                    ForgeToken.read_only.is_(read_only),
                    ForgeToken.expires_at > datetime.now(UTC) + timedelta(minutes=5),
                )
                .order_by(ForgeToken.expires_at.desc())
                .limit(1)
            )
            if cached is not None:
                return open_forge_token(cached), cached.expires_at.isoformat()
            await session.rollback()
            token, expires_at = await mint()
            username = self.binding.repo.split("/", 1)[0]
            session.add(
                ForgeToken(
                    project_id=self.binding.project_id,
                    api_url=self.binding.api_url,
                    username=username,
                    value=seal_forge_token(
                        self.binding.project_id, self.binding.api_url, username, token
                    ),
                    expires_at=expires_at,
                    read_only=read_only,
                )
            )
            await session.commit()
            return token, expires_at.isoformat()

    def _account(self) -> tuple[str, httpx.BasicAuth]:
        username = self.binding.repo.split("/", 1)[0]
        return username, httpx.BasicAuth(username, forge_password(self.binding))

    async def _scoped_token(self) -> tuple[str, datetime]:
        await self.revoke_expired_read_tokens()
        username, auth = self._account()
        expires_at = datetime.now(UTC) + READ_TOKEN_TTL
        async with httpx.AsyncClient(transport=self.transport, timeout=20) as client:
            response = await client.post(
                f"{self.binding.api_url}/users/{username}/tokens",
                auth=auth,
                json={
                    "name": f"{READ_TOKEN_PREFIX}{int(expires_at.timestamp())}",
                    "scopes": list(READ_SCOPES),
                },
            )
        if response.status_code != 201 or not isinstance(
            token := response.json().get("sha1"), str
        ):
            raise ForgejoTokenError(
                f"Forgejo refused a read-only token (HTTP {response.status_code})"
            )
        return token, expires_at

    async def revoke_expired_read_tokens(self) -> int:
        """Delete the project account's read-only tokens that are past their
        time, found by the expiry their name carries: a mint or a purge that
        failed midway leaves nothing a later one does not find. How many."""
        username, auth = self._account()
        now = datetime.now(UTC).timestamp()
        base = f"{self.binding.api_url}/users/{username}/tokens"
        revoked = 0
        async with httpx.AsyncClient(transport=self.transport, timeout=20) as client:
            response = await client.get(base, auth=auth, params={"limit": 50})
            if response.status_code != 200:
                raise ForgejoTokenError(
                    f"Forgejo did not list the account's tokens "
                    f"(HTTP {response.status_code})"
                )
            for listed in response.json():
                name = str(listed.get("name") or "")
                expiry = name.removeprefix(READ_TOKEN_PREFIX)
                if name == expiry or not expiry.isdigit() or int(expiry) > now:
                    continue
                gone = await client.delete(f"{base}/{listed['id']}", auth=auth)
                if gone.status_code in (204, 404):
                    revoked += 1
        return revoked

    async def write_token(self) -> tuple[str, str]:
        return await self.installation_token()

    async def granted_permissions(self) -> dict[str, str]:
        return {"project account": "owner"}

    async def read_permissions(self) -> dict[str, str]:
        return {scope.removeprefix("read:"): "read" for scope in READ_SCOPES}


async def purge_expired_tokens(
    sessions: SessionFactory, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, int]:
    """Remove expired cache entries. Upstream, the provider expires every token
    but the read-only ones on Forgejo, which this revokes first."""
    async with sessions() as session:
        projects = set(
            await session.scalars(
                select(ForgeToken.project_id).where(
                    ForgeToken.read_only.is_(True),
                    ForgeToken.expires_at <= datetime.now(UTC),
                )
            )
        )
        bindings = list(
            await session.scalars(
                select(ProjectForge).where(
                    ProjectForge.project_id.in_(projects),
                    ProjectForge.kind == "forgejo",
                )
            )
        )
    revoked = 0
    for binding in bindings:
        try:
            revoked += await ForgejoTokens(
                binding, sessions=sessions, transport=transport
            ).revoke_expired_read_tokens()
        except (ForgejoTokenError, httpx.HTTPError):
            # The next read-only mint for this project finds them by name.
            continue
    async with sessions() as session:
        ids = list(
            await session.scalars(
                delete(ForgeToken)
                .where(ForgeToken.expires_at <= datetime.now(UTC))
                .returning(ForgeToken.id)
            )
        )
        await session.commit()
    return {"deleted": len(ids), "revoked": revoked}
