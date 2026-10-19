"""A project's remote MCP servers: their state, connecting them, and calling them.

Everything credential-shaped stays in this module and the tables it writes. A
session reaches a server only through `call`, which attaches the credential
here, in the backend; the session host, the room's machine and the agent see
the server's answers and never the token or the secret values.

Declaring them means adding the servers the teammates' types declare, and which
types this project's seats are is read from the agent-instance tables — above
this package in the layer table. So the caller hands those two lookups in
(`remote_mcp/ports.py`), the same rule as the session factory in `webhook`.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import shlex
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import single_use_state
from app.core.config import settings
from app.core.crypto import DecryptionError, Purpose, decrypt, encrypt
from app.core.errors import NotFoundError, UnprocessableEntityError, ValidationError
from app.core.sentences import say
from app.domain.remote_mcp import declared, local, oauth, upstream
from app.domain.remote_mcp.declared import Declared, LocalServer, RemoteServer
from app.domain.remote_mcp.models import (
    ProjectMcpCommand,
    ProjectMcpConnection,
    ProjectMcpRun,
    ProjectMcpSecret,
    ProjectMcpServer,
)
from app.domain.remote_mcp.ports import DeviceOwners, ProjectTypes, TypeOfSeat

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


async def _added_rows(
    db: AsyncSession, project_id: uuid.UUID
) -> dict[str, ProjectMcpServer]:
    rows = await db.scalars(
        select(ProjectMcpServer)
        .where(ProjectMcpServer.project_id == project_id)
        .order_by(ProjectMcpServer.created_at)
    )
    return {row.name: row for row in rows}


async def _command_rows(
    db: AsyncSession, project_id: uuid.UUID
) -> dict[str, ProjectMcpCommand]:
    rows = await db.scalars(
        select(ProjectMcpCommand)
        .where(ProjectMcpCommand.project_id == project_id)
        .order_by(ProjectMcpCommand.created_at)
    )
    return {row.name: row for row in rows}


async def _with_added(
    db: AsyncSession, project_id: uuid.UUID, found: Declared
) -> Declared:
    added: list[RemoteServer | LocalServer] = [
        declared.settings_server(row.name, row.url, row.header_name)
        for row in (await _added_rows(db, project_id)).values()
    ]
    added += [
        local.added_server(row)
        for row in (await _command_rows(db, project_id)).values()
    ]
    return declared.with_settings(found, added)


async def _project_declared(
    db: AsyncSession,
    project_id: uuid.UUID,
    project_types: ProjectTypes,
    *,
    fresh: bool = False,
) -> Declared:
    """Every remote server anyone in the project may need connected: its
    `.mcp.json`'s, those added in its settings, and its teammates' types'."""

    return declared.with_types(
        await _with_added(
            db, project_id, await declared.read(db, project_id, fresh=fresh)
        ),
        await project_types(db, project_id),
    )


async def _seat_declared(
    db: AsyncSession,
    project_id: uuid.UUID,
    agent_handle: str | None,
    type_of_seat: TypeOfSeat,
) -> Declared:
    """The remote servers one teammate's sessions reach: the project's, and its
    own type's. A teammate of another type never reaches this type's."""

    agent_type = await type_of_seat(db, project_id, agent_handle)
    return declared.with_types(
        await _with_added(db, project_id, await declared.read(db, project_id)),
        [agent_type] if agent_type else [],
    )


async def seat_declared(
    db: AsyncSession,
    project_id: uuid.UUID,
    agent_handle: str | None,
    *,
    type_of_seat: TypeOfSeat,
) -> Declared:
    """The servers one teammate's sessions reach, remote and local."""
    return await _seat_declared(db, project_id, agent_handle, type_of_seat)


async def secret_values(db: AsyncSession, project_id: uuid.UUID) -> dict[str, str]:
    """Every project secret's value, for the caller to pick from."""
    return _secret_values(project_id, await _secret_rows(db, project_id))


@dataclass(frozen=True)
class SessionServers:
    """What a session in this project gets: the servers it can call, and the
    ones it cannot yet because somebody has to act in project settings."""

    usable: tuple[str, ...] = ()
    unusable: tuple[str, ...] = ()


async def session_servers(
    db: AsyncSession,
    project_id: uuid.UUID,
    agent_handle: str | None,
    *,
    type_of_seat: TypeOfSeat,
) -> SessionServers:
    found = await _seat_declared(db, project_id, agent_handle, type_of_seat)
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
    *,
    type_of_seat: TypeOfSeat,
) -> dict | None:
    """What the execution target of ``agent_handle``'s session carries about
    remote servers: where it posts their calls, and which it may call. None
    when there are none."""
    usable = (
        await session_servers(db, project_id, agent_handle, type_of_seat=type_of_seat)
    ).usable
    if not usable:
        return None
    return {"path": f"/topics/{topic_id}/mcp", "servers": list(usable)}


def _when(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


async def settings_view(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    project_types: ProjectTypes,
    device_owners: DeviceOwners,
) -> dict:
    """The settings page's list: one row per server, remote and then local,
    never a credential.

    Each row says where the server comes from: ``declared_by`` is None for the
    project's `.mcp.json`, or the teammates' types that define it. A local row
    says how the server last fared (``run``), naming the owner of the member's
    computer it waits on (``device_owners``: device ids to handles).
    """

    found: Declared = await _project_declared(db, project_id, project_types, fresh=True)
    added = await _added_rows(db, project_id)
    types = await project_types(db, project_id)
    connections = await _connections(db, project_id)
    secret_rows = await _secret_rows(db, project_id)
    values = _secret_values(project_id, secret_rows)
    servers = []
    shadowed = [
        declared.settings_server(row.name, row.url, row.header_name)
        for name, row in added.items()
        if name in found.shadowed
    ]
    for server in [*found.servers, *shadowed]:
        connection = connections.get(server.name)
        from_settings = server.name in found.added or server in shadowed
        row = added.get(server.name) if from_settings else None
        state = _status(server, connection, values)
        servers.append(
            {
                "name": server.name,
                "transport": server.transport,
                "host": _host(server.url),
                "command": None,
                "run": None,
                "auth": "oauth" if server.uses_oauth else "headers",
                "status": state,
                "added": {
                    "by": row.added_by,
                    "at": _when(row.created_at),
                    "url": row.url,
                    "header": row.header_name,
                    "env": None,
                }
                if row is not None
                else None,
                "shadowed": server in shadowed,
                "declared_by": None
                if server.name in found.names or row is not None
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
    runs = await local.runs(db, project_id)
    owners = await device_owners(
        db, sorted({r.device_id for r in runs.values() if r.device_id})
    )
    servers += local.rows(
        found, types, runs, owners, secret_rows, await _command_rows(db, project_id)
    )
    return {"servers": servers, "problem": found.problem}


async def room_view(
    db: AsyncSession, project_id: uuid.UUID, *, project_types: ProjectTypes
) -> list[dict]:
    """What a room's members see: each remote server, its state and who
    authorized it. The local ones are the settings page's."""

    async def nobody(db: AsyncSession, device_ids: Sequence[str]) -> dict[str, str]:
        return {}

    view = await settings_view(
        db, project_id, project_types=project_types, device_owners=nobody
    )
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
        if server["run"] is None
    ]


def _host(url: str) -> str:

    # A templated host is shown as written; its value may be a secret.
    return urlsplit(url).netloc or url


async def _declared_server(
    db: AsyncSession,
    project_id: uuid.UUID,
    name: str,
    project_types: ProjectTypes,
    *,
    fresh: bool = False,
) -> RemoteServer:
    server = (await _project_declared(db, project_id, project_types, fresh=fresh)).get(
        name
    )
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
    project_types: ProjectTypes,
) -> str:
    """Start the authorization; returns the URL the browser goes to."""
    server = await _declared_server(db, project_id, name, project_types, fresh=True)
    if not server.uses_oauth:
        raise UnprocessableEntityError(say("mcpServerUsesHeaderKey"))
    values = _secret_values(project_id, await _secret_rows(db, project_id))
    try:
        url, _ = server.expanded(values)
    except KeyError as missing:
        raise UnprocessableEntityError(
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
                "names_itself": found.names_itself,
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
        raise UnprocessableEntityError(say("mcpAuthLinkInvalid")) from None
    if flow.get("exp", 0) < time.time():
        raise UnprocessableEntityError(say("mcpAuthExpired"))
    return flow


async def finish_connect(
    db: AsyncSession, flow: dict, *, code: str, issuer: str | None
) -> None:
    """The authorization server sent the browser back with a code."""
    if not await single_use_state.claim(_STATE_SCOPE, flow["jti"]):
        raise UnprocessableEntityError(say("mcpAuthAlreadyUsed"))
    # RFC 9207: a response naming another issuer is a mix-up, not ours; so is
    # one naming none from a server that said it always names itself.
    if issuer and issuer.rstrip("/") != flow["issuer"].rstrip("/"):
        raise UnprocessableEntityError(say("mcpIssuerMismatch"))
    if not issuer and flow.get("names_itself"):
        raise UnprocessableEntityError(say("mcpIssuerMismatch"))
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
    except Exception as exc:  # the local disconnect still happens
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


# --- servers added in settings ------------------------------------------------------

_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _checked(name: str, url: str, header_name: str | None) -> None:
    if not _NAME.match(name):
        raise UnprocessableEntityError(say("mcpServerNameInvalid"))
    parts = urlsplit(url)
    if not parts.netloc or (
        parts.scheme != "https" and not settings.remote_mcp_allow_private_hosts
    ):
        raise UnprocessableEntityError(
            say("mcpServerNeedsHttps", host=parts.hostname or url)
        )
    if header_name is not None and not re.match(r"^[A-Za-z0-9-]{1,128}$", header_name):
        raise UnprocessableEntityError(say("mcpHeaderNameInvalid"))


async def _added_row(
    db: AsyncSession, project_id: uuid.UUID, name: str
) -> ProjectMcpServer:
    row = await db.scalar(
        select(ProjectMcpServer).where(
            ProjectMcpServer.project_id == project_id, ProjectMcpServer.name == name
        )
    )
    if row is None:
        raise NotFoundError(say("mcpServerNotAdded"))
    return row


async def _store_header(
    db: AsyncSession, project_id: uuid.UUID, name: str, value: str, handle: str
) -> None:
    variable = declared.settings_variable(name)
    row = await db.scalar(
        select(ProjectMcpSecret).where(
            ProjectMcpSecret.project_id == project_id,
            ProjectMcpSecret.name == variable,
        )
    )
    sealed = _seal(Purpose.MCP_SECRET, value, _secret_binding(project_id, variable))
    if row is None:
        db.add(
            ProjectMcpSecret(
                project_id=project_id, name=variable, value=sealed, updated_by=handle
            )
        )
    else:
        row.value, row.updated_by = sealed, handle


async def _forget_credentials(
    db: AsyncSession, project_id: uuid.UUID, name: str
) -> None:
    """Drop what was authorized for a server: its connection (revoked) and the
    header value it sent. Done when its address changes, so a token or a key
    issued for one host is never sent to another, and when it is removed."""
    row = await db.scalar(
        select(ProjectMcpConnection).where(
            ProjectMcpConnection.project_id == project_id,
            ProjectMcpConnection.server_name == name,
        )
    )
    if row is not None:
        await _revoke(row)
        await db.delete(row)
    await db.execute(
        delete(ProjectMcpSecret).where(
            ProjectMcpSecret.project_id == project_id,
            ProjectMcpSecret.name == declared.settings_variable(name),
        )
    )
    await upstream.forget((project_id, name))


async def add_server(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    name: str,
    url: str,
    header_name: str | None,
    header_value: str | None,
    handle: str,
    project_types: ProjectTypes,
) -> None:
    """Add a remote server in settings. Its name is one no other source of the
    project uses, so the server an agent calls by that name is not in doubt."""
    _checked(name, url, header_name)
    if header_name is not None and not header_value:
        raise UnprocessableEntityError(say("mcpValueRequired"))
    await _name_free(db, project_id, name, project_types)
    db.add(
        ProjectMcpServer(
            project_id=project_id,
            name=name,
            url=url,
            header_name=header_name,
            added_by=handle,
        )
    )
    # A secret left by an earlier server of this name is not this one's.
    await _forget_credentials(db, project_id, name)
    if header_name is not None and header_value:
        await _store_header(db, project_id, name, header_value, handle)
    await db.commit()


async def _name_free(
    db: AsyncSession, project_id: uuid.UUID, name: str, project_types: ProjectTypes
) -> None:
    """Refuse a name any source of the project already uses, so the server an
    agent calls by that name is not in doubt."""
    found = await _project_declared(db, project_id, project_types, fresh=True)
    if (
        name in found.names
        or found.get(name) is not None
        or found.get_local(name) is not None
        or name in found.shadowed
    ):
        raise UnprocessableEntityError(say("mcpServerNameTaken", name=name))


def _command(name: str, line: str, env: dict[str, str]) -> tuple[str, list[str]]:
    """A command added in settings, checked: its name, the line split as a
    shell would, and its environment's names."""
    if not _NAME.match(name):
        raise UnprocessableEntityError(say("mcpServerNameInvalid"))
    try:
        words = shlex.split(line)
    except ValueError:
        words = []
    if not words:
        raise UnprocessableEntityError(say("mcpCommandInvalid"))
    if any(not re.match(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$", key) for key in env):
        raise UnprocessableEntityError(say("mcpEnvNameInvalid"))
    return words[0], words[1:]


async def add_command(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    name: str,
    line: str,
    env: dict[str, str],
    handle: str,
    project_types: ProjectTypes,
) -> None:
    """Add a local server in settings: a command, run on the room's machine
    like one the repository's `.mcp.json` names, and asked about on a member's
    computer the same way."""
    command, args = _command(name, line, env)
    await _name_free(db, project_id, name, project_types)
    db.add(
        ProjectMcpCommand(
            project_id=project_id,
            name=name,
            command=command,
            args=args,
            env=env,
            added_by=handle,
        )
    )
    # How an earlier server of this name fared is not this one's.
    await _forget_run(db, project_id, name)
    await db.commit()


async def update_command(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    name: str,
    line: str,
    env: dict[str, str],
) -> None:
    """Change a command added in settings. A changed command is a new one to
    the owner of a member's computer, who is asked again."""
    command, args = _command(name, line, env)
    row = await _command_row(db, project_id, name)
    row.command, row.args, row.env = command, args, env
    await db.commit()


async def _command_row(
    db: AsyncSession, project_id: uuid.UUID, name: str
) -> ProjectMcpCommand:
    row = await db.scalar(
        select(ProjectMcpCommand).where(
            ProjectMcpCommand.project_id == project_id,
            ProjectMcpCommand.name == name,
        )
    )
    if row is None:
        raise NotFoundError(say("mcpServerNotAdded"))
    return row


async def added_by(db: AsyncSession, project_id: uuid.UUID, name: str) -> str | None:
    """Who added the command of this name in settings."""
    return await db.scalar(
        select(ProjectMcpCommand.added_by).where(
            ProjectMcpCommand.project_id == project_id,
            ProjectMcpCommand.name == name,
        )
    )


async def _forget_run(db: AsyncSession, project_id: uuid.UUID, name: str) -> None:
    await db.execute(
        delete(ProjectMcpRun).where(
            ProjectMcpRun.project_id == project_id, ProjectMcpRun.name == name
        )
    )


async def session_commands(db: AsyncSession, project_id: uuid.UUID) -> dict[str, dict]:
    """The commands added in settings that a session runs, as definitions: the
    room's machine is handed one with each call. One whose name the
    repository's `.mcp.json` uses is the repository's."""
    found = await _with_added(db, project_id, await declared.read(db, project_id))
    return {
        server.name: local.spec(server)
        for server in found.local
        if server.name in found.added
    }


async def update_server(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    name: str,
    url: str,
    header_name: str | None,
    header_value: str | None,
    handle: str,
) -> None:
    """Change a server added in settings. A new address or a new way of signing
    in forgets what was authorized for the old one."""
    _checked(name, url, header_name)
    row = await _added_row(db, project_id, name)
    if row.url != url or row.header_name != header_name:
        await _forget_credentials(db, project_id, name)
        if header_name is not None and not header_value:
            raise UnprocessableEntityError(say("mcpValueRequired"))
    row.url, row.header_name = url, header_name
    if header_name is not None and header_value:
        await _store_header(db, project_id, name, header_value, handle)
    await db.commit()


async def remove_server(db: AsyncSession, project_id: uuid.UUID, name: str) -> None:
    """Remove a server added in settings, remote or a command."""
    command = await db.scalar(
        select(ProjectMcpCommand).where(
            ProjectMcpCommand.project_id == project_id,
            ProjectMcpCommand.name == name,
        )
    )
    if command is not None:
        await _forget_run(db, project_id, name)
        await db.delete(command)
        await db.commit()
        return
    row = await _added_row(db, project_id, name)
    await _forget_credentials(db, project_id, name)
    await db.delete(row)
    await db.commit()


# --- secret values --------------------------------------------------------------


async def set_secret(
    db: AsyncSession,
    project_id: uuid.UUID,
    name: str,
    value: str,
    handle: str,
    *,
    project_types: ProjectTypes,
) -> None:
    found = await _project_declared(db, project_id, project_types, fresh=True)
    if not any(name in server.all_variables() for server in found.servers) and not any(
        name in local.variables(server) for server in found.local
    ):
        raise NotFoundError(say("mcpVariableNotUsed"))
    if not value:
        raise UnprocessableEntityError(say("mcpValueRequired"))
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
    type_of_seat: TypeOfSeat,
) -> dict:
    """One MCP request from ``agent_handle``'s session in `topic_id` to a server
    that session has: the project's, or its own type's."""
    server = (await _seat_declared(db, project_id, agent_handle, type_of_seat)).get(
        name
    )
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
