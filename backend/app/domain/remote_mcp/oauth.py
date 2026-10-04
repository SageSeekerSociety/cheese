"""The platform as an OAuth 2.1 client of remote MCP servers.

Follows the MCP authorization specification (2025-11-25): protected resource
metadata (RFC 9728) finds the authorization server, authorization server
metadata (RFC 8414 / OpenID Connect discovery) finds its endpoints, and the
authorization code grant runs with PKCE S256 and the `resource` parameter
(RFC 8707) on both the authorization and the token request. The client is, in
the specification's order: a client registered in advance for that issuer, a
Client ID Metadata Document on the platform's own origin, or one registered
dynamically (RFC 7591).
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
from dataclasses import dataclass
from urllib.parse import quote, urlencode, urlsplit

import httpx

from app.core.config import settings
from app.core.errors import ValidationError
from app.core.sentences import say
from app.domain.remote_mcp import http


class AuthorizationRefused(ValidationError):
    """The authorization server said no, or cannot be used as the spec needs."""


def redirect_uri() -> str:
    return f"{settings.frontend_url.rstrip('/')}/api/mcp/oauth/callback"


def client_metadata_url() -> str:
    return f"{settings.frontend_url.rstrip('/')}/api/mcp/oauth/client.json"


def client_metadata() -> dict:
    """The platform's Client ID Metadata Document: its `client_id` is its own URL."""
    return {
        "client_id": client_metadata_url(),
        "client_name": "Cheese",
        "client_uri": settings.frontend_url.rstrip("/"),
        "redirect_uris": [redirect_uri()],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }


@dataclass(frozen=True)
class Server:
    """What discovery found out about one MCP server's authorization."""

    resource: str
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None
    revocation_endpoint: str | None
    scope: str | None
    cimd: bool


@dataclass(frozen=True)
class Registration:
    client_id: str
    client_secret: str | None
    auth_method: str


@dataclass(frozen=True)
class Tokens:
    access_token: str
    refresh_token: str | None
    expires_in: int | None
    scope: str


_PARAM = re.compile(r'([A-Za-z_]+)="([^"]*)"')


def _challenge(header: str) -> dict[str, str]:
    """The parameters of a `Bearer` challenge in `WWW-Authenticate`."""
    return {k.lower(): v for k, v in _PARAM.findall(header or "")}


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _path(url: str) -> str:
    path = urlsplit(url).path
    return "" if path in ("", "/") else path.rstrip("/")


async def _json(client: httpx.AsyncClient, url: str) -> dict | None:
    try:
        response = await client.get(url, headers={"Accept": "application/json"})
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


async def discover(server_url: str, resource: str) -> Server:
    async with http.client() as client:
        challenge: dict[str, str] = {}
        try:
            probe = await client.post(
                server_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 0,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "cheese", "version": "1"},
                    },
                },
                headers={"Accept": "application/json, text/event-stream"},
            )
        except httpx.HTTPError as exc:
            raise AuthorizationRefused(say("mcpServerUnreachable", error=exc)) from exc
        if probe.status_code == 401:
            challenge = _challenge(probe.headers.get("www-authenticate", ""))
        origin, path = _origin(server_url), _path(server_url)
        candidates = (
            [challenge["resource_metadata"]] if "resource_metadata" in challenge else []
        )
        if path:
            candidates.append(f"{origin}/.well-known/oauth-protected-resource{path}")
        candidates.append(f"{origin}/.well-known/oauth-protected-resource")
        protected = None
        for url in candidates:
            if (protected := await _json(client, url)) is not None:
                break
        issuers = (protected or {}).get("authorization_servers") or [origin]
        issuer = str(issuers[0]).rstrip("/")
        metadata = await _authorization_metadata(client, issuer)
        if metadata is None:
            if protected is not None:
                raise AuthorizationRefused(say("mcpAuthNoMetadata"))
            # A server with neither document: the 2025-03-26 defaults.
            metadata = {
                "issuer": issuer,
                "authorization_endpoint": f"{issuer}/authorize",
                "token_endpoint": f"{issuer}/token",
                "registration_endpoint": f"{issuer}/register",
                "code_challenge_methods_supported": ["S256"],
            }
        if "S256" not in (metadata.get("code_challenge_methods_supported") or []):
            raise AuthorizationRefused(say("mcpAuthNoPkce"))
        if not metadata.get("authorization_endpoint") or not metadata.get(
            "token_endpoint"
        ):
            raise AuthorizationRefused(say("mcpAuthMetadataIncomplete"))
        scope = challenge.get("scope") or " ".join(
            (protected or {}).get("scopes_supported") or []
        )
        return Server(
            resource=resource,
            issuer=str(metadata.get("issuer") or issuer),
            authorization_endpoint=metadata["authorization_endpoint"],
            token_endpoint=metadata["token_endpoint"],
            registration_endpoint=metadata.get("registration_endpoint"),
            revocation_endpoint=metadata.get("revocation_endpoint"),
            scope=scope or None,
            cimd=bool(metadata.get("client_id_metadata_document_supported")),
        )


async def _authorization_metadata(
    client: httpx.AsyncClient, issuer: str
) -> dict | None:
    origin, path = _origin(issuer), _path(issuer)
    if path:
        urls = [
            f"{origin}/.well-known/oauth-authorization-server{path}",
            f"{origin}/.well-known/openid-configuration{path}",
            f"{origin}{path}/.well-known/openid-configuration",
        ]
    else:
        urls = [
            f"{origin}/.well-known/oauth-authorization-server",
            f"{origin}/.well-known/openid-configuration",
        ]
    for url in urls:
        if (found := await _json(client, url)) is not None:
            return found
    return None


async def register(server: Server) -> Registration:
    configured = settings.remote_mcp_oauth_clients.get(server.issuer) or (
        settings.remote_mcp_oauth_clients.get(server.issuer + "/")
    )
    if configured and configured.get("client_id"):
        secret = configured.get("client_secret") or None
        return Registration(
            client_id=configured["client_id"],
            client_secret=secret,
            auth_method=configured.get("token_endpoint_auth_method")
            or ("client_secret_basic" if secret else "none"),
        )
    # A metadata document has to be fetchable by the authorization server,
    # and the specification requires it to be served over HTTPS.
    if server.cimd and settings.frontend_url.startswith("https://"):
        return Registration(client_metadata_url(), None, "none")
    if not server.registration_endpoint:
        raise AuthorizationRefused(say("mcpNeedsPreregisteredClient"))
    async with http.client() as client:
        try:
            response = await client.post(
                server.registration_endpoint,
                json={
                    "client_name": "Cheese",
                    "redirect_uris": [redirect_uri()],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "none",
                },
            )
        except httpx.HTTPError as exc:
            raise AuthorizationRefused(
                say("mcpClientRegistrationFailed", error=exc)
            ) from exc
    if response.status_code not in (200, 201):
        raise AuthorizationRefused(
            say("mcpClientRegistrationRefused", status=response.status_code)
        )
    data = response.json()
    return Registration(
        client_id=data["client_id"],
        client_secret=data.get("client_secret") or None,
        auth_method=data.get("token_endpoint_auth_method")
        or ("client_secret_basic" if data.get("client_secret") else "none"),
    )


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    return verifier, challenge


def authorization_url(
    server: Server, registration: Registration, *, state: str, challenge: str
) -> str:
    query = {
        "response_type": "code",
        "client_id": registration.client_id,
        "redirect_uri": redirect_uri(),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "resource": server.resource,
    }
    if server.scope:
        query["scope"] = server.scope
    separator = "&" if "?" in server.authorization_endpoint else "?"
    return server.authorization_endpoint + separator + urlencode(query)


async def _token_request(
    token_endpoint: str,
    form: dict[str, str],
    *,
    client_id: str,
    client_secret: str | None,
    auth_method: str,
) -> Tokens:
    headers = {"Accept": "application/json"}
    body = dict(form)
    _authenticate(body, headers, client_id, client_secret, auth_method)
    async with http.client() as client:
        try:
            response = await client.post(token_endpoint, data=body, headers=headers)
        except httpx.HTTPError as exc:
            raise AuthorizationRefused(
                say("mcpAuthServerUnreachable", error=exc)
            ) from exc
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code != 200 or not data.get("access_token"):
        raise AuthorizationRefused(
            say(
                "mcpTokenRequestRefused",
                error=str(data.get("error") or response.status_code),
            )
        )
    return Tokens(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token"),
        expires_in=int(data["expires_in"]) if data.get("expires_in") else None,
        scope=str(data.get("scope") or ""),
    )


def _authenticate(
    body: dict[str, str],
    headers: dict[str, str],
    client_id: str,
    client_secret: str | None,
    auth_method: str,
) -> None:
    """Client authentication at the token and revocation endpoints."""
    if auth_method == "client_secret_basic" and client_secret:
        pair = f"{quote(client_id, safe='')}:{quote(client_secret, safe='')}"
        headers["Authorization"] = "Basic " + base64.b64encode(pair.encode()).decode()
        return
    body["client_id"] = client_id
    if auth_method == "client_secret_post" and client_secret:
        body["client_secret"] = client_secret


async def exchange(
    *,
    token_endpoint: str,
    code: str,
    verifier: str,
    resource: str,
    client_id: str,
    client_secret: str | None,
    auth_method: str,
) -> Tokens:
    return await _token_request(
        token_endpoint,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri(),
            "code_verifier": verifier,
            "resource": resource,
        },
        client_id=client_id,
        client_secret=client_secret,
        auth_method=auth_method,
    )


async def refresh(
    *,
    token_endpoint: str,
    refresh_token: str,
    resource: str,
    client_id: str,
    client_secret: str | None,
    auth_method: str,
) -> Tokens:
    return await _token_request(
        token_endpoint,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "resource": resource,
        },
        client_id=client_id,
        client_secret=client_secret,
        auth_method=auth_method,
    )


async def revoke(
    *,
    revocation_endpoint: str,
    token: str,
    hint: str,
    client_id: str,
    client_secret: str | None,
    auth_method: str,
) -> int:
    """RFC 7009. The authorization server's HTTP status; 200 means it accepted."""
    headers: dict[str, str] = {}
    body = {"token": token, "token_type_hint": hint}
    _authenticate(body, headers, client_id, client_secret, auth_method)
    async with http.client() as client:
        response = await client.post(revocation_endpoint, data=body, headers=headers)
    return response.status_code
