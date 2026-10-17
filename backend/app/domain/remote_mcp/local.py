"""A project's local MCP servers: a command each, run on the room's machine —
the repository's `.mcp.json` ones, those added in its settings, and its
teammates' types'.

The platform does not run them, the machine's executor does, from its own
checkout's definition. What the platform keeps is what a person needs around
that:

- **which project secrets one may be handed.** A `${VAR}` a local server's
  definition names is filled from the project's secrets when it starts, and the
  value is then on the room's machine, where the agent can read it — the
  settings page says so. Only the variables the *default branch's* definition
  of that name references are handed over: a branch can write any `${VAR}`
  into its own `.mcp.json`, and a definition read from the room's checkout
  would let it name any secret the project has. The variable that holds the
  header of a server added in settings is never handed to a command.
- **how one last fared** (``ProjectMcpRun``), as the session's gateway reported
  it, for the settings page. A report of a server running is only believed for
  ``RUNNING_FOR``: a gateway that died with its session cannot say it stopped.
- **whether the owner of a member's computer lets a command run on it**
  (``ProjectMcpApproval``). The computer checks it itself, against the
  fingerprints ``allowed`` hands the gateway, over the command it is actually
  about to run; one it does not find is reported back, and becomes a question
  in the owner's 「等你处理」. The platform's own machines ask nobody.

What a device is, and whose, is the caller's to say (``Machine``): devices are
a layer above this one.
"""

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError
from app.core.sentences import say
from app.domain.agent_type.library import AgentTypeDef
from app.domain.remote_mcp.declared import Declared, LocalServer
from app.domain.remote_mcp.models import (
    ProjectMcpApproval,
    ProjectMcpCommand,
    ProjectMcpRun,
    ProjectMcpSecret,
)

#: The states a gateway reports, and the two an owner's answer leaves.
RUNNING, FAILED, AWAITING, REFUSED, IDLE = (
    "running",
    "failed",
    "awaiting",
    "refused",
    "idle",
)
REPORTED = {RUNNING, FAILED, AWAITING, IDLE}
#: How long a report that a server runs is believed without another.
RUNNING_FOR = timedelta(hours=12)
#: How many of the last lines a failure keeps.
LOG_LINES = 12
#: Where the variables of servers added in settings live (`declared`).
SETTINGS_PREFIX = "CHEESE_MCP_"


@dataclass(frozen=True)
class Machine:
    """The computer a session's server runs on, as the caller knows it."""

    device_id: str
    #: The handle of its owner.
    owner: str
    #: A member's own computer, rather than one of the platform's.
    member: bool


def added_server(row: ProjectMcpCommand) -> LocalServer:
    """The server a command added in settings is."""
    return LocalServer(
        name=row.name,
        command=row.command,
        args=tuple(str(a) for a in row.args or []),
        env={str(k): str(v) for k, v in (row.env or {}).items()},
    )


def spec(server: LocalServer) -> dict:
    """A local server's definition as `.mcp.json` writes it, which is what the
    room's machine is handed to start it."""
    found: dict = {"command": server.command}
    if server.args:
        found["args"] = list(server.args)
    if server.env:
        found["env"] = dict(server.env)
    return found


def handed(name: str) -> bool:
    """Whether a project secret of this name may be handed to a command."""
    return not name.startswith(SETTINGS_PREFIX)


def variables(server: LocalServer | None) -> list[str]:
    """The secrets a local server of the default branch may be handed."""
    return [n for n in server.all_variables() if handed(n)] if server else []


def values(server: LocalServer | None, secrets: dict[str, str]) -> dict[str, str]:
    """The values to start ``server`` with: those of its own variables that
    are set, and nothing else."""
    return {name: secrets[name] for name in variables(server) if name in secrets}


async def runs(db: AsyncSession, project_id: uuid.UUID) -> dict[str, ProjectMcpRun]:
    rows = await db.scalars(
        select(ProjectMcpRun).where(ProjectMcpRun.project_id == project_id)
    )
    return {row.name: row for row in rows}


def run_view(row: ProjectMcpRun | None, owners: dict[str, str]) -> dict:
    """What the settings page says about how a server last fared."""
    if row is None:
        return {"state": IDLE, "log": [], "owner": None}
    state = row.state
    if state == RUNNING and datetime.now(UTC) - row.updated_at > RUNNING_FOR:
        state = IDLE
    return {
        "state": state,
        "log": row.log.splitlines() if state == FAILED else [],
        "owner": owners.get(row.device_id) if row.device_id else None,
    }


def rows(
    found: Declared,
    types: Sequence[AgentTypeDef],
    runs_by_name: dict[str, ProjectMcpRun],
    owners: dict[str, str],
    secret_rows: dict[str, ProjectMcpSecret],
    added: dict[str, ProjectMcpCommand],
) -> list[dict]:
    """The settings page's row for each local server, in the remote rows'
    shape (``settings_view``). A command added in settings whose name the
    repository's `.mcp.json` also uses is listed too, as shadowed."""
    titles = {agent_type.name: agent_type.title for agent_type in types}
    shadowed = [
        added_server(row) for name, row in added.items() if name in found.shadowed
    ]
    out = []
    for server in [*found.local, *shadowed]:
        names = variables(server)
        missing = [n for n in server.variables() if handed(n) and n not in secret_rows]
        declaring = found.local_types.get(server.name)
        row = (
            added.get(server.name)
            if server.name in found.added or server in shadowed
            else None
        )
        out.append(
            {
                "name": server.name,
                "transport": "stdio",
                "host": "",
                "command": server.line(),
                "auth": "headers",
                "status": "missing_values" if missing else "ready",
                "run": run_view(runs_by_name.get(server.name), owners),
                "added": {
                    "by": row.added_by,
                    "at": row.created_at.isoformat(),
                    "url": None,
                    "header": None,
                    "env": dict(server.env),
                }
                if row is not None
                else None,
                "shadowed": server in shadowed,
                "declared_by": None
                if not declaring or row is not None
                else [{"name": n, "title": titles.get(n, n)} for n in declaring],
                "authorized_by": None,
                "authorized_at": None,
                "variables": [
                    {
                        "name": name,
                        "set": name in secret_rows,
                        "updated_by": secret_rows[name].updated_by
                        if name in secret_rows
                        else None,
                        "updated_at": secret_rows[name].updated_at.isoformat()
                        if name in secret_rows
                        else None,
                    }
                    for name in names
                ],
            }
        )
    return out


async def allowed(
    db: AsyncSession, project_id: uuid.UUID, machine: Machine | None
) -> list[str] | None:
    """The fingerprints of the commands the owner of a member's computer lets
    run on it; None where nobody is asked (the platform's machines)."""
    if machine is None or not machine.member:
        return None
    found = await db.scalars(
        select(ProjectMcpApproval.fingerprint).where(
            ProjectMcpApproval.project_id == project_id,
            ProjectMcpApproval.device_id == machine.device_id,
            ProjectMcpApproval.allowed.is_(True),
        )
    )
    return sorted(found)


async def decided(db: AsyncSession, project_id: uuid.UUID) -> str | None:
    """A digest of every answer the project's owners have given, for a
    session's launch: an answer changes it, so an idle session starts again
    with the command it may now run. None before anyone answered."""
    found = await db.execute(
        select(
            ProjectMcpApproval.device_id,
            ProjectMcpApproval.fingerprint,
            ProjectMcpApproval.allowed,
        ).where(
            ProjectMcpApproval.project_id == project_id,
            ProjectMcpApproval.allowed.is_not(None),
        )
    )
    answers = sorted(f"{d}:{f}:{a}" for d, f, a in found)
    return hashlib.sha256("\n".join(answers).encode()).hexdigest() if answers else None


async def report(
    db: AsyncSession,
    project_id: uuid.UUID,
    name: str,
    *,
    state: str,
    log: list[str],
    machine: Machine | None,
    fingerprint: str | None = None,
    command: str | None = None,
    source: str = "repository",
    author: str | None = None,
) -> str:
    """Keep what a gateway said about one of the project's local servers, and
    answer with the state the settings page now shows. A command waiting for
    the owner of a member's computer becomes a question to them, once per
    command; one they refused stays refused."""
    if state not in REPORTED:
        raise ValueError(f"Unknown state {state!r}")  # i18n-exempt: to the gateway
    if state == AWAITING:
        if machine is None or not machine.member or not fingerprint:
            # i18n-exempt: said to the session's gateway, never to a person
            raise ValueError("Only a member's computer waits for its owner")
        approval = await db.scalar(
            select(ProjectMcpApproval).where(
                ProjectMcpApproval.project_id == project_id,
                ProjectMcpApproval.device_id == machine.device_id,
                ProjectMcpApproval.fingerprint == fingerprint,
            )
        )
        if approval is None:
            db.add(
                ProjectMcpApproval(
                    project_id=project_id,
                    device_id=machine.device_id,
                    name=name,
                    command=(command or "")[:4000],
                    fingerprint=fingerprint,
                    source=source,
                    author=author,
                )
            )
        elif approval.allowed is False:
            state = REFUSED
    row = await db.scalar(
        select(ProjectMcpRun).where(
            ProjectMcpRun.project_id == project_id, ProjectMcpRun.name == name
        )
    )
    if row is None:
        row = ProjectMcpRun(project_id=project_id, name=name, state=state, log="")
        db.add(row)
    row.state = state
    row.log = "\n".join(log[-LOG_LINES:]) if state == FAILED else ""
    row.device_id = machine.device_id if machine and machine.member else None
    row.updated_at = datetime.now(UTC)
    await db.flush()
    return state


async def decide(
    db: AsyncSession, approval_id: uuid.UUID, *, owner: str, owns, allow: bool
) -> ProjectMcpApproval:
    """The owner's answer to whether a command runs on their computer.
    ``owns(device_id)`` says whether ``owner`` owns that computer."""
    approval = await db.get(ProjectMcpApproval, approval_id)
    if approval is None:
        raise NotFoundError(say("mcpApprovalNotFound"))
    if not await owns(approval.device_id):
        raise ForbiddenError(say("mcpApprovalOwnerOnly"))
    approval.allowed = allow
    approval.decided_by = owner
    approval.decided_at = datetime.now(UTC)
    row = await db.scalar(
        select(ProjectMcpRun).where(
            ProjectMcpRun.project_id == approval.project_id,
            ProjectMcpRun.name == approval.name,
            ProjectMcpRun.device_id == approval.device_id,
        )
    )
    if row is not None and row.state in (AWAITING, REFUSED):
        row.state = IDLE if allow else REFUSED
        row.updated_at = datetime.now(UTC)
    await db.flush()
    return approval


async def waiting_for(
    db: AsyncSession, device_ids: list[str]
) -> list[ProjectMcpApproval]:
    """The questions still open on these computers, newest first."""
    if not device_ids:
        return []
    found = await db.scalars(
        select(ProjectMcpApproval)
        .where(
            ProjectMcpApproval.device_id.in_(device_ids),
            ProjectMcpApproval.allowed.is_(None),
        )
        .order_by(ProjectMcpApproval.created_at.desc())
    )
    return list(found)
