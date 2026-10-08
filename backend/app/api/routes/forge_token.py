"""Project-scoped credentials for native forge CLI invocations.

Root-mounted like ``/llm`` and the other machine-facing routers: everything a
sandbox calls is built as ``{connector_public_base}/<path>``, which maps onto
the backend root, not ``/api``.

The response carries the project's forge, repository, expiry, and actual grants.
The provider's administrative credentials never leave the backend.
"""

import asyncio
import base64
import binascii
import hmac
import uuid
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit

import httpx
from fastapi import (
    APIRouter,
    Depends,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_seated_agent
from app.api.response import ok
from app.api.routes.llm_tunnel import _pump_tcp_to_ws, _pump_ws_to_tcp
from app.core.db import get_db
from app.core.errors import (
    AuthenticationRequiredError,
    ForbiddenError,
    GatewayUnavailableError,
)
from app.core.sandbox_auth import scoped_token_claims
from app.core.sentences import say
from app.domain.agent.forgejo_tokens import ForgejoTokenError, open_forge_token
from app.domain.agent.github_app import GitHubAppError
from app.domain.project.forge import binding_for_project, tokens_for_project
from app.domain.project.models import ForgeToken
from app.domain.room_task.place import session_keeps_work

router = APIRouter(prefix="/sandbox", tags=["sandbox"])


@router.websocket("/forge-tunnel/{project_id}")
async def forge_tunnel(
    project_id: uuid.UUID,
    websocket: WebSocket,
    token: str = "",
    db: AsyncSession = Depends(get_db),
) -> None:
    """Carry native GitHub HTTPS through the deployment without terminating TLS."""

    claims = scoped_token_claims(token)
    if not claims or claims.get("p") != str(project_id):
        await websocket.close(code=1008)
        return
    if not await _seated(db, token, project_id):
        await websocket.close(code=1008)
        return
    binding = await binding_for_project(project_id, db)
    if binding is None or binding.kind != "github_app":
        await websocket.close(code=1008)
        return
    hosts = {urlsplit(binding.url).hostname, urlsplit(binding.api_url).hostname}
    public_github = "github.com" in hosts
    await db.rollback()
    await websocket.accept()
    writer = None
    tasks = []
    try:
        async with asyncio.timeout(15):
            head = bytearray()
            while b"\r\n\r\n" not in head:
                head.extend(await websocket.receive_bytes())
                if len(head) > 16384:
                    raise ValueError("CONNECT header too large")
            header, _, remainder = bytes(head).partition(b"\r\n\r\n")
            method, destination, version = header.split(b"\r\n", 1)[0].decode().split()
            target = urlsplit("//" + destination)
            host = target.hostname or ""
            # GitHub serves release assets and Actions logs on these origins.
            # https://docs.github.com/en/actions/reference/runners/self-hosted-runners
            download_host = public_github and host.endswith(
                (".github.com", ".githubusercontent.com", ".blob.core.windows.net")
            )
            if (
                method != "CONNECT"
                or version != "HTTP/1.1"
                or target.port != 443
                or target.username is not None
                or target.path
                or target.query
                or target.fragment
                or not (host in hosts or download_host)
            ):
                await websocket.send_bytes(b"HTTP/1.1 403 Forbidden\r\n\r\n")
                return
            reader, writer = await asyncio.open_connection(host, 443)
        await websocket.send_bytes(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        if remainder:
            writer.write(remainder)
            await writer.drain()
        tasks = [
            asyncio.create_task(_pump_ws_to_tcp(websocket, writer)),
            asyncio.create_task(_pump_tcp_to_ws(reader, websocket)),
        ]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except (OSError, TimeoutError, ValueError, WebSocketDisconnect):
        pass
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
        try:
            await websocket.close()
        except (RuntimeError, OSError, WebSocketDisconnect):
            pass


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
        if not await _seated(db, credential, project_id):
            return Response(status_code=403)
        repository = binding.repo + ".git/"
        if not path.startswith(repository) or path[len(repository) :] not in (
            "info/refs",
            "git-upload-pack",
            "git-receive-pack",
        ):
            return Response(status_code=403)
        # A session whose work is not kept fetches and never pushes. A push is
        # advertised (`info/refs?service=git-receive-pack`) before it is sent.
        kept = await session_keeps_work(db, claims.get("session"))
        pushing = path.endswith("git-receive-pack") or (
            request.query_params.get("service") == "git-receive-pack"
        )
        if pushing and not kept:
            return Response(status_code=403)
        minter = await tokens_for_project(project_id, db)
        if minter is None:
            raise GatewayUnavailableError(say("forgeCredentialMissing"))
        try:
            access_token, _ = await (
                minter.installation_token() if kept else minter.read_token()
            )
        except (GitHubAppError, httpx.HTTPError) as error:
            raise GatewayUnavailableError(say("forgeCredentialUnavailable")) from error
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
            select(ForgeToken).where(
                ForgeToken.project_id == project_id,
                ForgeToken.api_url == binding.api_url,
                ForgeToken.username == binding.repo.split("/", 1)[0],
                ForgeToken.expires_at > datetime.now(UTC),
                ForgeToken.value.is_not(None),
            )
        )
        if not any(
            hmac.compare_digest(credential.encode(), open_forge_token(lease).encode())
            for lease in leases
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
            raise GatewayUnavailableError(
                say("forgeTokenServiceUnreachable")
            ) from error
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
                if key
                in (
                    "content-type",
                    "content-encoding",
                    "content-length",
                    "x-total-count",
                )
            },
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


async def _seated(db: AsyncSession, token: str, project_id: uuid.UUID) -> bool:
    """Whether the agent this credential names still sits in the project.

    The git relay and tunnel answer protocol errors, not JSON, so the refusal
    ``require_seated_agent`` raises is turned into a yes/no here."""
    try:
        await require_seated_agent(db, token, project_id=project_id, topic_id=None)
    except (AuthenticationRequiredError, ForbiddenError):
        return False
    return True


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

    token = _caller_token(request)
    claims = scoped_token_claims(token) if token else None
    if not claims or not claims.get("p"):
        raise AuthenticationRequiredError("A scoped cheese token is required")
    project_id = uuid.UUID(claims["p"])
    await require_seated_agent(db, token, project_id=project_id, topic_id=None)
    # A 支线, or a task not yet started, keeps nothing it does
    # (`Place.keeps_work`): its token reads the repository, its issues and pull
    # requests, and the forge refuses its pushes and its writes.
    kept = await session_keeps_work(db, claims.get("session"))
    binding = await binding_for_project(project_id, db)
    minter = await tokens_for_project(project_id, db)
    if binding is None or minter is None:
        raise GatewayUnavailableError(say("forgeCredentialMissing"))
    try:
        if kept:
            access_token, expires_at = await minter.installation_token()
            granted = await minter.granted_permissions()
        else:
            access_token, expires_at = await minter.read_token()
            granted = await minter.read_permissions()
    except (GitHubAppError, ForgejoTokenError, httpx.HTTPError) as error:
        raise GatewayUnavailableError(say("forgeCredentialUnavailable")) from error
    response.headers["Cache-Control"] = "no-store"
    return ok(
        {
            "kind": binding.kind,
            "project_id": str(project_id),
            "token": access_token,
            "expires_at": expires_at,
            # Forgejo's read-only tokens are revoked by the platform.
            "expiry_enforcement": "platform"
            if not kept and binding.kind == "forgejo"
            else "provider",
            "read_only": not kept,
            "repo": binding.repo,
            "url": binding.url,
            "default_branch": binding.default_branch,
            "api_url": binding.api_url,
            "username": "x-access-token"
            if binding.kind == "github_app"
            else binding.repo.split("/", 1)[0],
            "permissions": ", ".join(
                f"{key}: {value}" for key, value in sorted(granted.items())
            ),
        }
    )
