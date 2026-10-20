"""Status and logout for credentials with their own team and member ownership.

The owner logs it in on the machine with `cheesehost claude login`, with the
build the platform pins. Each run adds a login of its own, in a directory of
the platform's: the first one a machine had in `place.CLAUDE_LOGIN_DIR`, the
rest under `place.CLAUDE_LOGINS_DIR`, one directory each, named by the login's
id within the enrollment. Each is a model connection of its own
(`DeviceClaudeLoginRow` records its team separately from its storage transport).
An existing enrollment profile can carry the transport; a model-only host can
carry it without enrolling an execution device. Each profile has separate login
directories.

The machine is the only one that knows whether a login is still good, so the
backend asks it — `claude auth status` for each, which answers without calling
a model — and keeps the answers: when the machine connects, and when its owner
asks again.

The question travels as a program on stdin (`hub.exec(["python3", "-"])`), the
way every other one-off question to a machine does, so it runs the same on
Linux, macOS and Windows, where the connector provides `python3`.

Its owner can log a subscription out from any browser (「模型连接」), through
the connector's `cheesehost claude logout <id>` and the same `hub.exec`.
A logout asked for while the machine is
away is recorded (``revoked_at``) and carried out as it next connects; the
login is out of use from the moment it is asked for.
"""

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.harness.claude_code.device_launch import CLAUDE_PINNED_VERSION
from app.domain.agent.place import (
    CLAUDE_KEYCHAIN_DIR,
    CLAUDE_LOGIN_DIR,
    CLAUDE_LOGINS_DIR,
    footprint_root,
)
from app.domain.agent_instance.own import rename_for_connection
from app.domain.device import claude_login
from app.domain.device.login_kind import FIRST_LOGIN, LOGIN_ID
from app.domain.device.models import (
    DeviceClaudeLoginRow,
    DeviceRow,
    ModelCredentialHost,
)
from app.domain.device.supply import Supply

logger = logging.getLogger(__name__)

# What the machine runs. It must not need anything of ours: the pinned build is
# where the connector put it (`cli/internal/claudecode`), and the environment is
# cleared of what would point Claude Code at another login, as the connector
# clears it for `cheesehost claude login`. The first login is always reported,
# as it was before a machine could hold several, so a machine with the build
# and no login still says it has the build. The logins are asked at once: each
# `auth status` may take seconds.
_PROGRAM = """
import getpass, json, os, re, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

root = Path.home() / ROOT
login_root = Path.home() / os.environ.get("CHEESE_PROFILE_ROOT", ROOT)
suffix = ".exe" if sys.platform == "win32" else ""
binary = root / "claude" / "versions" / (VERSION + suffix)
if not binary.is_file():
    print(json.dumps({"installed": False, "logins": []}))
    raise SystemExit
env = {
    k: v
    for k, v in os.environ.items()
    if k not in ("CLAUDE_CONFIG_DIR", "CLAUDE_SECURESTORAGE_CONFIG_DIR",
                 "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")
}
if not env.get("USER"):
    try:
        env["USER"] = getpass.getuser()
    except Exception:
        pass


def report(login):
    local_id, directory = login
    result = subprocess.run(
        [str(binary), "auth", "status"],
        env={**env, "CLAUDE_CONFIG_DIR": str(directory),
             "CLAUDE_SECURESTORAGE_CONFIG_DIR": str(directory),
             "PATH": str(directory / KEYCHAIN) + os.pathsep + env.get("PATH", "")},
        capture_output=True, text=True, timeout=60,
    )
    try:
        status = json.loads(result.stdout)
    except ValueError:
        status = {"loggedIn": False}
    return {"id": local_id, "status": status}


logins = [(FIRST, login_root / LOGIN)]
more = login_root / LOGINS
if more.is_dir():
    logins += [
        (d.name, d) for d in sorted(more.iterdir())
        if d.is_dir() and re.fullmatch(LOGIN_ID, d.name)
    ]
with ThreadPoolExecutor(max_workers=8) as pool:
    reports = list(pool.map(report, logins))
print(json.dumps({"installed": True, "logins": reports}))
"""


def program() -> str:
    return (
        f"ROOT = {footprint_root()!r}\n"
        f"LOGIN = {CLAUDE_LOGIN_DIR!r}\n"
        f"LOGINS = {CLAUDE_LOGINS_DIR!r}\n"
        f"FIRST = {FIRST_LOGIN!r}\n"
        f"LOGIN_ID = {LOGIN_ID.pattern!r}\n"
        f"KEYCHAIN = {CLAUDE_KEYCHAIN_DIR!r}\n"
        f"VERSION = {CLAUDE_PINNED_VERSION!r}\n" + _PROGRAM
    )


@dataclass(frozen=True)
class ClaudeLogin:
    installed: bool
    logged_in: bool
    auth_method: str | None = None
    subscription_type: str | None = None
    #: What the machine calls this login (its directory's name there).
    local_id: str = FIRST_LOGIN


def _one(local_id: str, entry: dict) -> ClaudeLogin:
    status = entry.get("status")
    if not isinstance(status, dict):
        status = {}
    logged_in = (
        status.get("loggedIn") is True and status.get("authMethod") == "claude.ai"
    )

    def short(name: str) -> str | None:
        value = status.get(name)
        return value[:32] if logged_in and isinstance(value, str) and value else None

    return ClaudeLogin(
        installed=True,
        logged_in=logged_in,
        auth_method=short("authMethod"),
        subscription_type=short("subscriptionType"),
        local_id=local_id,
    )


def read_answer(stdout: str) -> list[ClaudeLogin]:
    """What the machine's program printed, as its logins. Anything unreadable
    is one first login, not logged in: a login nobody can confirm is not one to
    start a session on. So is a machine without the build."""
    nothing = [ClaudeLogin(installed=False, logged_in=False)]
    try:
        answer = json.loads(stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return nothing
    if not isinstance(answer, dict) or answer.get("installed") is not True:
        return nothing
    entries = answer.get("logins")
    if not isinstance(entries, list):
        return nothing
    logins: dict[str, ClaudeLogin] = {}
    for entry in entries:
        local_id = entry.get("id") if isinstance(entry, dict) else None
        # Its id goes into a path on the machine when a session launches with
        # it (`device_launch._OWN_LOGIN_CREDENTIALS`).
        if isinstance(local_id, str) and LOGIN_ID.fullmatch(local_id):
            logins[local_id] = _one(local_id, entry)
    return list(logins.values()) or nothing


async def ask(hub, device_id: str) -> list[ClaudeLogin]:
    """Ask the machine. Raises what ``hub.exec`` raises when it is not there."""
    result = await hub.exec(device_id, ["python3", "-"], stdin=program(), timeout=90)
    if result.get("exit") != 0:
        logger.warning(
            "claude login probe failed on %s: %s",
            device_id,
            str(result.get("stderr") or "")[-500:],
        )
        return read_answer("")
    return read_answer(str(result.get("stdout") or ""))


async def remember(
    session: AsyncSession, device_id: str, logins: Sequence[ClaudeLogin]
) -> None:
    """Keep what the machine said about each of its logins. A login it no
    longer has (logged out and removed there) is kept as logged out."""
    now = datetime.now(UTC)
    for login in logins:
        values = {
            "installed": login.installed,
            "logged_in": login.logged_in,
            "auth_method": login.auth_method,
            "subscription_type": login.subscription_type,
            "checked_at": now,
        }
        row = await session.scalar(
            select(DeviceClaudeLoginRow).where(
                or_(
                    DeviceClaudeLoginRow.device_id == device_id,
                    DeviceClaudeLoginRow.host_id == device_id,
                ),
                DeviceClaudeLoginRow.local_id == login.local_id,
            )
        )
        # Only an explicitly registered credential has a team. A probe reports
        # status; it cannot turn an execution device into a model credential.
        if row is None:
            continue
        for key, value in values.items():
            setattr(row, key, value)
        if row.revoked_at is not None:
            row.logged_in = False
        login_id = row.id
        if login.logged_in:
            # The own agents on this connection are named after it (its plan
            # or model), which the owner may just have switched.
            await rename_for_connection(session, login_id)
    await session.execute(
        update(DeviceClaudeLoginRow)
        .where(
            or_(
                DeviceClaudeLoginRow.device_id == device_id,
                DeviceClaudeLoginRow.host_id == device_id,
            ),
            DeviceClaudeLoginRow.local_id.not_in([login.local_id for login in logins]),
            DeviceClaudeLoginRow.logged_in.is_(True),
        )
        .values(logged_in=False, checked_at=now)
    )


async def rename(session: AsyncSession, login_id: str, name: str | None) -> bool:
    """Its owner names one of their logins; the own agents on it are named
    after it from now on, whatever the machine reports later. False when it is
    not a login in use."""
    row = await session.get(DeviceClaudeLoginRow, login_id)
    if row is None or not (row.logged_in and row.revoked_at is None):
        return False
    row.name = name or None
    await session.flush()
    await rename_for_connection(session, login_id)
    return True


async def status(session: AsyncSession, login_id: str) -> dict | None:
    """What the machine last said about one login, for its owner to read; None
    when there is no such login."""
    row = await session.get(DeviceClaudeLoginRow, login_id, populate_existing=True)
    if row is None:
        return None
    return claude_login.view(row)


async def machine_of(session: AsyncSession, login_id: str) -> str | None:
    """The machine holding a login; None when there is no such login."""
    return await session.scalar(
        select(
            func.coalesce(DeviceClaudeLoginRow.host_id, DeviceClaudeLoginRow.device_id)
        ).where(DeviceClaudeLoginRow.id == login_id)
    )


class MachineRefused(Exception):
    """The machine ran the connector's command and it failed; the message is
    the last line the command printed to stderr."""


#: The name a command to the machine uses for the machine's own connector,
#: which runs it as itself under its own config (`cli/internal/host`).
CONNECTOR = "cheesehost"


async def _run(hub, device_id: str, argv: list[str], **kwargs) -> None:
    result = await hub.exec(device_id, [CONNECTOR, *argv], **kwargs)
    if result.get("exit") != 0:
        said = [
            line.strip()
            for line in str(result.get("stderr") or "").splitlines()
            if line.strip()
        ]
        raise MachineRefused(said[-1] if said else "")


async def log_out(hub, device_id: str, local_id: str) -> None:
    """Log one of the platform's Claude Code logins out on the machine and
    forget its model service, as `cheesehost claude logout <id>` does there.
    The owner's own Claude Code (``~/.claude``) and the other logins are not
    touched."""
    await _run(hub, device_id, ["claude", "logout", local_id], timeout=90)


async def settle(session: AsyncSession, hub, device_id: str) -> None:
    """Carry out the removals its owner made while the machine was away: log
    each of those logins out there, then clear its removal. Raises what the
    machine raises, leaving the removals not yet carried out for its next
    connection."""
    pending = (
        await session.scalars(
            select(DeviceClaudeLoginRow).where(
                or_(
                    DeviceClaudeLoginRow.device_id == device_id,
                    DeviceClaudeLoginRow.host_id == device_id,
                ),
                DeviceClaudeLoginRow.revoked_at.is_not(None),
            )
        )
    ).all()
    for row in pending:
        await log_out(hub, device_id, row.local_id)
        row.revoked_at = None
        row.logged_in = False
        row.checked_at = datetime.now(UTC)
        await session.commit()


async def revoke(session: AsyncSession, hub, login_id: str) -> None:
    """Its owner removes one of their logins, from wherever they are. It is
    out of use from this moment — no session starts with it — and its machine
    logs it out now if it is online, or as it next connects. The name its owner
    gave it goes with it: a login made there later is another connection."""
    row = await session.get(DeviceClaudeLoginRow, login_id)
    if row is None:
        return
    device_id = row.transport_id
    row.revoked_at, row.logged_in, row.name = datetime.now(UTC), False, None
    await session.commit()
    if not hub.is_online(device_id):
        return
    try:
        await settle(session, hub, device_id)
    except Exception:
        logger.warning(
            "claude logout left for the next connection of %s",
            device_id,
            exc_info=True,
        )


async def refresh(session_factory, hub, device_id: str) -> None:
    """Carry out pending removals, then ask the machine and keep its answer."""
    async with session_factory() as session:
        try:
            await settle(session, hub, device_id)
        except Exception:
            logger.warning(
                "claude logout not carried out on %s", device_id, exc_info=True
            )
    logins = await ask(hub, device_id)
    async with session_factory() as session:
        await remember(session, device_id, logins)
        await session.commit()


async def refresh_on_connect(session_factory, hub, device_id: str) -> None:
    """Settle and ask a machine a person enrolled, as it connects. Cloud
    machines are the platform's and carry no one's login. A machine that goes
    again before it answers is asked on its next connection."""
    async with session_factory() as session:
        device = await session.get(DeviceRow, device_id)
        host = await session.get(ModelCredentialHost, device_id)
        if host is None and (device is None or device.supply != Supply.self_hosted):
            return
    try:
        await refresh(session_factory, hub, device_id)
    except Exception:
        logger.warning("claude login not checked on %s", device_id, exc_info=True)


async def set_models(session: AsyncSession, login_id: str, models: list[str]) -> None:
    """Set a device connection's team-approved models without changing credentials."""
    row = await session.get(DeviceClaudeLoginRow, login_id)
    if row is not None:
        row.allowed_models = list(dict.fromkeys(models))
