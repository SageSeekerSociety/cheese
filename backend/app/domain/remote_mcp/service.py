"""A project's remote MCP servers: their state, connecting them, and calling them.

Everything credential-shaped stays in this module and the tables it writes. A
session reaches a server only through `call`, which attaches the credential
here, in the backend; the session host, the room's machine and the agent see
the server's answers and never the token or the secret values.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import single_use_state
from app.core.crypto import DecryptionError, Purpose, decrypt, encrypt
from app.core.errors import NotFoundError, ValidationError
from app.domain.block.notice_text import say
from app.domain.remote_mcp import declared, oauth, upstream
from app.domain.remote_mcp.declared import Declared, RemoteServer
from app.domain.remote_mcp.models import ProjectMcpConnection, ProjectMcpSecret

logger = logging.getLogger(__name__)

_STATE_SCOPE = "remote-mcp-oauth"
_STATE_TTL_S = 600
#: Refresh this long before the access token says it expires.
_REFRESH_MARGIN = timedelta(seconds=60)

# Status codes the settings page and the room render.
CONNECTED = "connected"
DISCONNECTED = "disconnected"
NEEDS_RECONNECT = "needs_reconnect"
MISSING_VALUES = "missing_values"
READY = "ready"
USABLE = frozenset({CONNECTED, READY})


class NotConnected(ValidationError):
    """The server cannot be called until someone connects it in settings."""


def _seal(purpose: Purpose, value: str, bound_to: str) -> str:
    return encrypt(purpose, value, bound_to=bound_to)


def _token_binding(project_id: uuid.UUID, name: str, column: str) -> str:
    return f"mcp:{project_id}:{name}:{column}"


def _secret_binding(project_id: uuid.UUID, name: str) -> str:
    return f"mcp-secret:{project_id}:{name}"


def _open(row: ProjectMcpConnection, column: str) -> str | None:
    stored = getattr(row, column)
    if not stored:
        return None
    return decrypt(
        Purpose.MCP_OAUTH_TOKEN,
        stored,
        bound_to=_token_binding(row.project_id, row.server_name, column),
    )


async def _connections(
    db: AsyncSession, project_id: uuid.UUID
) -> dict[str, ProjectMcpConnection]:
    rows = await db.scalars(
        select(ProjectMcpConnection).where(
            ProjectMcpConnection.project_id == project_id
        )
    )
    return {row.server_name: row for row in rows}


async def _secret_rows(
    db: AsyncSession, project_id: uuid.UUID
) -> dict[str, ProjectMcpSecret]:
    rows = await db.scalars(
        select(ProjectMcpSecret).where(ProjectMcpSecret.project_id == project_id)
    )
    return {row.name: row for row in rows}


def _secret_values(
    project_id: uuid.UUID, rows: dict[str, ProjectMcpSecret]
) -> dict[str, str]:
    values = {}
    for name, row in rows.items():
        try:
            values[name] = decrypt(
                Purpose.MCP_SECRET,
                row.value,
                bound_to=_secret_binding(project_id, name),
            )
        except DecryptionError:
            logger.warning(
                "remote_mcp: secret %s unreadable project=%s", name, project_id
            )
    return values


def _status(
    server: RemoteServer,
    connection: ProjectMcpConnection | None,
    values: dict[str, str],
) -> str:
    try:
        url, _ = server.expanded(values)
    except KeyError:
        return MISSING_VALUES
    if not server.uses_oauth:
        return READY
    if connection is None:
        return DISCONNECTED
    if connection.needs_reconnect or connection.server_url != url:
        return NEEDS_RECONNECT
    return CONNECTED


async def _project_declared(
    db: AsyncSession, project_id: uuid.UUID, *, fresh: bool = False
) -> Declared:
    """Every remote server anyone in the project may need connected: its
    `.mcp.json`'s and those of its teammates' types."""
    from app.domain.agent_instance.services import project_types

    return declared.with_types(
        await declared.read(db, project_id, fresh=fresh),
        await project_types(db, project_id),
    )


async def _seat_declared(
    db: AsyncSession, project_id: uuid.UUID, agent_handle: str | None
) -> Declared:
    """The remote servers one teammate's sessions reach: the project's, and its
    own type's. A teammate of another type never reaches this type's."""
    from app.domain.agent_instance.services import type_of_seat

    agent_type = await type_of_seat(db, project_id, agent_handle)
    return declared.with_types(
        await declared.read(db, project_id), [agent_type] if agent_type else []
    )


@dataclass(frozen=True)
class SessionServers:
    """What a session in this project gets: the servers it can call, and the
    ones it cannot yet because somebody has to act in project settings."""

    usable: tuple[str, ...] = ()
    unusable: tuple[str, ...] = ()


async def session_servers(
    db: AsyncSession, project_id: uuid.UUID, agent_handle: str | None
) -> SessionServers:
    found = await _seat_declared(db, project_id, agent_handle)
    if not found.servers:
        return SessionServers()
    connections = await _connections(db, project_id)
    values = _secret_values(project_id, await _secret_rows(db, project_id))
    usable, unusable = [], []
    for server in found.servers:
        state = _status(server, connections.get(server.name), values)
        (usable if state in USABLE else unusable).append(server.name)
    return SessionServers(tuple(usable), tuple(unusable))


async def session_target(
    db: AsyncSession,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    agent_handle: str | None,
) -> dict | None:
    """What the execution target of ``agent_handle``'s session carries about
    remote servers: where it posts their calls, and which it may call. None
    when there are none."""
    usable = (await session_servers(db, project_id, agent_handle)).usable
    if not usable:
        return None
    return {"path": f"/topics/{topic_id}/mcp", "servers": list(usable)}


def _when(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


async def settings_view(db: AsyncSession, project_id: uuid.UUID) -> dict:
    """The settings page's list: one row per remote server, never a credential.

    Each row says where the server comes from: ``declared_by`` is None for the
    project's `.mcp.json`, or the teammates' types that define it.
    """
    from app.domain.agent_instance.services import project_types

    found: Declared = await _project_declared(db, project_id, fresh=True)
    types = await project_types(db, project_id)
    connections = await _connections(db, project_id)
    secret_rows = await _secret_rows(db, project_id)
    values = _secret_values(project_id, secret_rows)
    servers = []
    for server in found.servers:
        connection = connections.get(server.name)
        state = _status(server, connection, values)
        servers.append(
            {
                "name": server.name,
                "transport": server.transport,
                "host": _host(server.url),
                "auth": "oauth" if server.uses_oauth else "headers",
                "status": state,
                "declared_by": None
                if server.name in found.names
                else [
                    {"name": agent_type.name, "title": agent_type.title}
                    for agent_type in types
                    if isinstance(
                        agent_type.inline_servers().get(server.name, {}).get("url"),
                        str,
                    )
                ],
                "authorized_by": connection.authorized_by
                if connection and server.uses_oauth
                else None,
                "authorized_at": _when(connection.authorized_at)
                if connection and server.uses_oauth
                else None,
                "variables": [
                    {
                        "name": name,
                        "set": name in secret_rows,
                        "updated_by": secret_rows[name].updated_by
                        if name in secret_rows
                        else None,
                        "updated_at": _when(secret_rows[name].updated_at)
                        if name in secret_rows
                        else None,
                    }
                    for name in server.all_variables()
                ],
            }
        )
    return {"servers": servers, "problem": found.problem}


async def room_view(db: AsyncSession, project_id: uuid.UUID) -> list[dict]:
    """What a room's members see: each server, its state and who authorized it."""
    view = await settings_view(db, project_id)
    return [
        {
            key: server[key]
            for key in (
                "name",
                "host",
                "auth",
                "status",
                "declared_by",
                "authorized_by",
                "authorized_at",
            )
        }
        for server in view["servers"]
    ]


def _host(url: str) -> str:
    from urllib.parse import urlsplit

    # A templated host is shown as written; its value may be a secret.
    return urlsplit(url).netloc or url


async def _declared_server(
    db: AsyncSession, project_id: uuid.UUID, name: str, *, fresh: bool = False
) -> RemoteServer:
    server = (await _project_declared(db, project_id, fresh=fresh)).get(name)
    if server is None:
        raise NotFoundError(say("mcpServerNotDeclared"))
    return server


# --- connecting ---------------------------------------------------------------


async def begin_connect(
    db: AsyncSession,
    project_id: uuid.UUID,
    name: str,
    handle: str,
    *,
    in_app: bool = False,
) -> str:
    """Start the authorization; returns the URL the browser goes to."""
    server = await _declared_server(db, project_id, name, fresh=True)
    if not server.uses_oauth:
        raise ValidationError(say("mcpServerUsesHeaderKey"))
    values = _secret_values(project_id, await _secret_rows(db, project_id))
    try:
        url, _ = server.expanded(values)
    except KeyError as missing:
        raise ValidationError(
            say("mcpFillVariableFirst", variable=missing.args[0])
        ) from None
    await db.rollback()  # nothing below needs the database
    resource = declared.canonical_resource(url)
    found = await oauth.discover(url, resource)
    registration = await oauth.register(found)
    verifier, challenge = oauth.pkce()
    jti = secrets.token_urlsafe(16)
    state = _seal(
        Purpose.MCP_OAUTH_STATE,
        json.dumps(
            {
                "jti": jti,
                "exp": int(time.time()) + _STATE_TTL_S,
                "project": str(project_id),
                "server": name,
                "url": url,
                "resource": resource,
                "handle": handle,
                "app": in_app,
                "verifier": verifier,
                "issuer": found.issuer,
                "token_endpoint": found.token_endpoint,
                "revocation_endpoint": found.revocation_endpoint,
                "client_id": registration.client_id,
                "client_secret": registration.client_secret,
                "auth_method": registration.auth_method,
            }
        ),
        "remote-mcp-state",
    )
    await single_use_state.reserve(_STATE_SCOPE, jti, ttl_s=_STATE_TTL_S)
    return oauth.authorization_url(
        found, registration, state=state, challenge=challenge
    )


def read_state(state: str) -> dict:
    try:
        flow = json.loads(
            decrypt(Purpose.MCP_OAUTH_STATE, state, bound_to="remote-mcp-state")
        )
    except (DecryptionError, ValueError):
        raise ValidationError(say("mcpAuthLinkInvalid")) from None
    if flow.get("exp", 0) < time.time():
        raise ValidationError(say("mcpAuthExpired"))
    return flow


async def finish_connect(
    db: AsyncSession, flow: dict, *, code: str, issuer: str | None
) -> None:
    """The authorization server sent the browser back with a code."""
    if not await single_use_state.claim(_STATE_SCOPE, flow["jti"]):
        raise ValidationError(say("mcpAuthAlreadyUsed"))
    # RFC 9207: a response naming another issuer is a mix-up, not ours.
    if issuer and issuer.rstrip("/") != flow["issuer"].rstrip("/"):
        raise ValidationError(say("mcpIssuerMismatch"))
    tokens = await oauth.exchange(
        token_endpoint=flow["token_endpoint"],
        code=code,
        verifier=flow["verifier"],
        resource=flow["resource"],
        client_id=flow["client_id"],
        client_secret=flow["client_secret"],
        auth_method=flow["auth_method"],
    )
    project_id, name = uuid.UUID(flow["project"]), flow["server"]
    previous = await db.scalar(
        select(ProjectMcpConnection)
        .where(
            ProjectMcpConnection.project_id == project_id,
            ProjectMcpConnection.server_name == name,
        )
        .with_for_update()
    )
    if previous is not None:
        await _revoke(previous)
        await db.delete(previous)
        await db.flush()
    now = datetime.now(UTC)

    def seal(column: str, value: str | None) -> str | None:
        if value is None:
            return None
        return _seal(
            Purpose.MCP_OAUTH_TOKEN, value, _token_binding(project_id, name, column)
        )

    db.add(
        ProjectMcpConnection(
            project_id=project_id,
            server_name=name,
            server_url=flow["url"],
            resource=flow["resource"],
            issuer=flow["issuer"],
            token_endpoint=flow["token_endpoint"],
            revocation_endpoint=flow["revocation_endpoint"],
            client_id=flow["client_id"],
            client_secret=seal("client_secret", flow["client_secret"]),
            token_endpoint_auth_method=flow["auth_method"],
            access_token=seal("access_token", tokens.access_token),
            refresh_token=seal("refresh_token", tokens.refresh_token),
            expires_at=now + timedelta(seconds=tokens.expires_in)
            if tokens.expires_in
            else None,
            scope=tokens.scope,
            authorized_by=flow["handle"],
            authorized_at=now,
        )
    )
    await db.commit()
    await upstream.forget((project_id, name))


async def _revoke(row: ProjectMcpConnection) -> None:
    """Best effort: an authorization server without a revocation endpoint, or
    one that is down, leaves the tokens to expire on their own."""
    if not row.revocation_endpoint:
        return
    try:
        secret = _open(row, "client_secret")
        for column, hint in (
            ("refresh_token", "refresh_token"),
            ("access_token", "access_token"),
        ):
            token = _open(row, column)
            if token:
                status = await oauth.revoke(
                    revocation_endpoint=row.revocation_endpoint,
                    token=token,
                    hint=hint,
                    client_id=row.client_id,
                    client_secret=secret,
                    auth_method=row.token_endpoint_auth_method,
                )
                if status != 200:
                    # The grant may still be valid upstream though we forget it.
                    logger.warning(
                        "remote_mcp: revocation refused server=%s kind=%s status=%s",
                        row.server_name,
                        hint,
                        status,
                    )
    except Exception as exc:  # noqa: BLE001 — the local disconnect still happens
        logger.warning(
            "remote_mcp: revoke failed server=%s error=%s",
            row.server_name,
            type(exc).__name__,
        )


async def disconnect(db: AsyncSession, project_id: uuid.UUID, name: str) -> None:
    row = await db.scalar(
        select(ProjectMcpConnection)
        .where(
            ProjectMcpConnection.project_id == project_id,
            ProjectMcpConnection.server_name == name,
        )
        .with_for_update()
    )
    if row is None:
        raise NotFoundError(say("mcpServerNotConnected"))
    await _revoke(row)
    await db.delete(row)
    await db.commit()
    await upstream.forget((project_id, name))


# --- secret values --------------------------------------------------------------


async def set_secret(
    db: AsyncSession, project_id: uuid.UUID, name: str, value: str, handle: str
) -> None:
    found = await _project_declared(db, project_id, fresh=True)
    if not any(name in server.all_variables() for server in found.servers):
        raise NotFoundError(say("mcpVariableNotUsed"))
    if not value:
        raise ValidationError(say("mcpValueRequired"))
    row = await db.scalar(
        select(ProjectMcpSecret).where(
            ProjectMcpSecret.project_id == project_id, ProjectMcpSecret.name == name
        )
    )
    sealed = _seal(Purpose.MCP_SECRET, value, _secret_binding(project_id, name))
    if row is None:
        db.add(
            ProjectMcpSecret(
                project_id=project_id, name=name, value=sealed, updated_by=handle
            )
        )
    else:
        row.value, row.updated_by = sealed, handle
    await db.commit()


async def clear_secret(db: AsyncSession, project_id: uuid.UUID, name: str) -> None:
    await db.execute(
        delete(ProjectMcpSecret).where(
            ProjectMcpSecret.project_id == project_id, ProjectMcpSecret.name == name
        )
    )
    await db.commit()


# --- calling ----------------------------------------------------------------------


async def _bearer(
    db: AsyncSession,
    project_id: uuid.UUID,
    name: str,
    url: str,
    *,
    rejected: str | None = None,
) -> str:
    """A current access token for the server, refreshed when due. `rejected` is
    a token the server just refused: refresh unless another request already
    replaced it."""
    row = await db.scalar(
        select(ProjectMcpConnection)
        .where(
            ProjectMcpConnection.project_id == project_id,
            ProjectMcpConnection.server_name == name,
        )
        .with_for_update()
    )
    if row is None:
        raise NotConnected(say("mcpConnectInSettings", server=name))
    if row.needs_reconnect or row.server_url != url:
        raise NotConnected(say("mcpReconnectInSettings", server=name))
    token = _open(row, "access_token")
    assert token is not None
    due = (
        row.expires_at is not None
        and row.expires_at - _REFRESH_MARGIN <= datetime.now(UTC)
    )
    if not due and token != rejected:
        await db.commit()
        return token
    refresh_token = _open(row, "refresh_token")
    if not refresh_token:
        row.needs_reconnect = True
        await db.commit()
        raise NotConnected(say("mcpServerAuthExpired", server=name))
    try:
        tokens = await oauth.refresh(
            token_endpoint=row.token_endpoint,
            refresh_token=refresh_token,
            resource=row.resource,
            client_id=row.client_id,
            client_secret=_open(row, "client_secret"),
            auth_method=row.token_endpoint_auth_method,
        )
    except oauth.AuthorizationRefused:
        row.needs_reconnect = True
        await db.commit()
        raise NotConnected(say("mcpAuthInvalid", server=name)) from None
    binding = _token_binding(project_id, name, "access_token")
    row.access_token = _seal(Purpose.MCP_OAUTH_TOKEN, tokens.access_token, binding)
    if tokens.refresh_token:
        row.refresh_token = _seal(
            Purpose.MCP_OAUTH_TOKEN,
            tokens.refresh_token,
            _token_binding(project_id, name, "refresh_token"),
        )
    row.expires_at = (
        datetime.now(UTC) + timedelta(seconds=tokens.expires_in)
        if tokens.expires_in
        else None
    )
    await db.commit()
    return tokens.access_token


async def call(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    agent_handle: str | None,
    name: str,
    method: str,
    params: dict,
) -> dict:
    """One MCP request from ``agent_handle``'s session in `topic_id` to a server
    that session has: the project's, or its own type's."""
    server = (await _seat_declared(db, project_id, agent_handle)).get(name)
    if server is None:
        raise NotFoundError(say("mcpServerNotOnTeammate"))
    values = _secret_values(project_id, await _secret_rows(db, project_id))
    try:
        url, headers = server.expanded(values)
    except KeyError as missing:
        raise NotConnected(
            say("mcpFillInSettings", server=name, field=missing.args[0])
        ) from None
    key = (project_id, name, topic_id)
    if not server.uses_oauth:
        await db.rollback()
        return await upstream.request(
            key,
            transport=server.transport,
            url=url,
            auth=headers,
            method=method,
            params=params,
        )
    token = await _bearer(db, project_id, name, url)
    try:
        return await upstream.request(
            key,
            transport=server.transport,
            url=url,
            auth={"Authorization": f"Bearer {token}"},
            method=method,
            params=params,
        )
    except upstream.Unauthorized:
        token = await _bearer(db, project_id, name, url, rejected=token)
        await upstream.forget(key)
    try:
        return await upstream.request(
            key,
            transport=server.transport,
            url=url,
            auth={"Authorization": f"Bearer {token}"},
            method=method,
            params=params,
        )
    except upstream.Unauthorized:
        raise NotConnected(say("mcpAuthRefused", server=name)) from None
