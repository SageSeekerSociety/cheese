"""Project-scoped credentials for native forge CLI invocations.

Root-mounted like ``/llm`` and the other machine-facing routers: everything a
sandbox calls is built as ``{connector_public_base}/<path>``, which maps onto
the backend root, not ``/api``.

The response carries the project's forge, repository, expiry, and actual grants.
The provider's administrative credentials never leave the backend.
"""

import base64
import binascii
import hmac
import uuid
from datetime import UTC, datetime
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok
from app.core.db import get_db
from app.core.errors import (
    AuthenticationRequiredError,
    GatewayUnavailableError,
)
from app.core.sandbox_auth import scoped_token_claims
from app.domain.agent.forgejo_tokens import ForgejoTokenError
from app.domain.agent.github_app import GitHubAppError

router = APIRouter(prefix="/sandbox", tags=["sandbox"])


def _forge_credential(authorization: str) -> str:
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() in ("token", "bearer"):
        return value.strip()
    if scheme.lower() == "basic":
        try:
            return base64.b64decode(value, validate=True).decode().partition(":")[2]
        except (ValueError, UnicodeError, binascii.Error):
            return ""
    return ""


@router.api_route(
    "/forge/{project_id}/{path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
async def forge_transport(
    project_id: uuid.UUID,
    path: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Relay native Git and API traffic using the caller's leased forge token."""
    from app.core.crypto import decrypt_text
    from app.domain.project.forge import binding_for_project, tokens_for_project
    from app.domain.project.models import ForgeToken

    authorization = request.headers.get("authorization", "")
    credential = _forge_credential(authorization)
    # Native Git first discovers whether the server requires HTTP Basic auth.
    denied = Response(
        status_code=401,
        headers={
            "WWW-Authenticate": 'Basic realm="Cheese forge"',
            "Cache-Control": "no-store",
        },
    )
    if not credential:
        return denied
    binding = await binding_for_project(project_id, db)
    if binding is None:
        return denied
    if binding.kind == "github_app":
        claims = scoped_token_claims(credential)
        if not claims or claims.get("p") != str(project_id):
            return denied
        repository = binding.repo + ".git/"
        if not path.startswith(repository) or path[len(repository) :] not in (
            "info/refs",
            "git-upload-pack",
            "git-receive-pack",
        ):
            return Response(status_code=403)
        minter = await tokens_for_project(project_id, db)
        if minter is None:
            raise GatewayUnavailableError("项目的代码托管凭据尚未配置")
        try:
            access_token, _ = await minter.installation_token()
        except (GitHubAppError, httpx.HTTPError) as error:
            raise GatewayUnavailableError("代码托管服务暂时无法签发项目凭据") from error
        authorization = (
            "Basic "
            + base64.b64encode(f"x-access-token:{access_token}".encode()).decode()
        )
        upstream_url = (
            binding.url.removesuffix(".git").rstrip("/")
            + ".git/"
            + path[len(repository) :]
        )
    elif binding.kind == "forgejo":
        leases = await db.scalars(
            select(ForgeToken.value).where(
                ForgeToken.project_id == project_id,
                ForgeToken.api_url == binding.api_url,
                ForgeToken.username == binding.repo.split("/", 1)[0],
                ForgeToken.expires_at > datetime.now(UTC),
                ForgeToken.value.is_not(None),
            )
        )
        if not any(
            hmac.compare_digest(credential.encode(), decrypt_text(value).encode())
            for value in leases
            if value is not None
        ):
            return denied
        base = binding.api_url.removesuffix("/api/v1").rstrip("/")
        upstream_url = base + "/" + quote(path, safe="/")
    else:
        return denied
    if any(part in (".", "..") for part in path.split("/")):
        return Response(status_code=400)
    # A long clone must not hold a database connection for its entire transfer.
    await db.rollback()
    headers = {
        key: value
        for key, value in request.headers.items()
        if key
        in (
            "authorization",
            "content-type",
            "content-encoding",
            "accept",
            "git-protocol",
            "user-agent",
        )
    }
    headers["authorization"] = authorization
    client = httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10))
    outgoing = client.build_request(
        request.method,
        upstream_url,
        headers=headers,
        params=tuple(request.query_params.multi_items()),
        content=request.stream(),
    )
    try:
        upstream = await client.send(outgoing, stream=True)
    except BaseException as error:
        await client.aclose()
        if isinstance(error, httpx.HTTPError):
            raise GatewayUnavailableError("代码托管服务暂时无法连接") from error
        raise

    async def body():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        body(),
        status_code=upstream.status_code,
        headers={
            **{
                key: value
                for key, value in upstream.headers.items()
                if key in ("content-type", "content-encoding", "content-length")
            },
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


def _caller_token(request: Request) -> str:
    """The scoped token, however the caller presents it: the cheese CLI sends
    X-Cheese-Token; a bearer header also works."""
    direct = request.headers.get("x-cheese-token", "").strip()
    if direct:
        return direct
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


@router.get("/forge-token")
async def sandbox_forge_token(
    request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> dict:
    from app.domain.project.forge import binding_for_project, tokens_for_project

    token = _caller_token(request)
    claims = scoped_token_claims(token) if token else None
    if not claims or not claims.get("p"):
        raise AuthenticationRequiredError("A scoped cheese token is required")
    project_id = uuid.UUID(claims["p"])
    binding = await binding_for_project(project_id, db)
    minter = await tokens_for_project(project_id, db)
    if binding is None or minter is None:
        raise GatewayUnavailableError("项目的代码托管凭据尚未配置")
    try:
        access_token, expires_at = await minter.installation_token()
        granted = await minter.granted_permissions()
    except (GitHubAppError, ForgejoTokenError, httpx.HTTPError) as error:
        raise GatewayUnavailableError("代码托管服务暂时无法签发项目凭据") from error
    response.headers["Cache-Control"] = "no-store"
    return ok(
        {
            "kind": binding.kind,
            "project_id": str(project_id),
            "token": access_token,
            "expires_at": expires_at,
            "expiry_enforcement": "provider",
            "repo": binding.repo,
            "url": binding.url,
            "api_url": binding.api_url,
            "username": "x-access-token"
            if binding.kind == "github_app"
            else binding.repo.split("/", 1)[0],
            "permissions": ", ".join(
                f"{key}: {value}" for key, value in sorted(granted.items())
            ),
        }
    )
