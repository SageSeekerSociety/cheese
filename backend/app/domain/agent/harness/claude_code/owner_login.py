"""Whether a machine's owner has logged in their own Claude Code for the platform.

The owner logs it in on the machine with `cheesehost claude login`, under a
directory of the platform's (`place.CLAUDE_LOGIN_DIR`) and with the build the
platform pins. The machine is the only one that knows whether that login is
still good, so the backend asks it — `claude auth status`, which answers without
calling a model — and keeps the answer (`DeviceClaudeLoginRow`): when the
machine connects, and before a session of the owner's Claude Code starts there.

The question travels as a program on stdin (`hub.exec(["python3", "-"])`), the
way every other one-off question to a machine does, so it runs the same on
Linux, macOS and Windows, where the connector provides `python3`.

Its owner can also change it from any browser (「模型连接」): log it out, or
point it at another model service. Those run the connector's own
`cheesehost claude logout` and `cheesehost claude login --base-url` on the
machine, through the same `hub.exec`. A logout asked for while the machine is
away is recorded (``revoked_at``) and carried out as it next connects; the
login is out of use from the moment it is asked for.
"""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import case, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.harness.claude_code.device_launch import CLAUDE_PINNED_VERSION
from app.domain.agent.place import (
    CLAUDE_LOGIN_DIR,
    MODEL_SERVICE_FILE,
    footprint_root,
)
from app.domain.device import claude_login
from app.domain.device.login_kind import MODEL_SERVICE
from app.domain.device.models import DeviceClaudeLoginRow, DeviceRow
from app.domain.device.supply import Supply

logger = logging.getLogger(__name__)

# What the machine runs. It must not need anything of ours: the pinned build is
# where the connector put it (`cli/internal/claudecode`), and the environment is
# cleared of what would point Claude Code at another login, as the connector
# clears it for `cheesehost claude login`.
_PROGRAM = """
import getpass, json, os, subprocess, sys
from pathlib import Path

root = Path.home() / ROOT
suffix = ".exe" if sys.platform == "win32" else ""
binary = root / "claude" / "versions" / (VERSION + suffix)
if not binary.is_file():
    print(json.dumps({"installed": False}))
    raise SystemExit
# Another model service the owner set (`cheesehost claude login --base-url`):
# the sessions call it and not the login. Its model and host leave the
# machine, for its owner to tell services apart; its key does not.
try:
    service = json.loads((root / LOGIN / SERVICE).read_text())
except (OSError, ValueError):
    service = None
if isinstance(service, dict) and service.get("base_url") and service.get("token"):
    from urllib.parse import urlsplit
    try:
        host = urlsplit(str(service["base_url"])).hostname
    except ValueError:
        host = None
    print(json.dumps({"installed": True,
                      "service": {"model": service.get("model"), "host": host}}))
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
env["CLAUDE_CONFIG_DIR"] = str(root / LOGIN)
result = subprocess.run(
    [str(binary), "auth", "status"], env=env, capture_output=True, text=True, timeout=60
)
try:
    status = json.loads(result.stdout)
except ValueError:
    status = {"loggedIn": False}
print(json.dumps({"installed": True, "status": status}))
"""


def program() -> str:
    return (
        f"ROOT = {footprint_root()!r}\n"
        f"LOGIN = {CLAUDE_LOGIN_DIR!r}\n"
        f"SERVICE = {MODEL_SERVICE_FILE!r}\n"
        f"VERSION = {CLAUDE_PINNED_VERSION!r}\n" + _PROGRAM
    )


@dataclass(frozen=True)
class ClaudeLogin:
    installed: bool
    logged_in: bool
    auth_method: str | None = None
    subscription_type: str | None = None
    #: The model a model service is called with; None for a Claude account.
    model: str | None = None
    #: The host of that model service; None for a Claude account.
    service_host: str | None = None


def read_answer(stdout: str) -> ClaudeLogin:
    """What the machine's program printed, as a login. Anything unreadable is
    "not logged in": a login nobody can confirm is not one to start a session
    on."""
    try:
        answer = json.loads(stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return ClaudeLogin(installed=False, logged_in=False)
    if not isinstance(answer, dict) or not answer.get("installed"):
        return ClaudeLogin(installed=False, logged_in=False)
    service = answer.get("service")
    if isinstance(service, dict):
        model, host = service.get("model"), service.get("host")
        return ClaudeLogin(
            installed=True,
            logged_in=True,
            auth_method=MODEL_SERVICE,
            model=model[:128] if isinstance(model, str) and model else None,
            service_host=host[:255] if isinstance(host, str) and host else None,
        )
    status = answer.get("status")
    if not isinstance(status, dict):
        status = {}
    logged_in = status.get("loggedIn") is True

    def short(name: str) -> str | None:
        value = status.get(name)
        return value[:32] if logged_in and isinstance(value, str) and value else None

    return ClaudeLogin(
        installed=True,
        logged_in=logged_in,
        auth_method=short("authMethod"),
        subscription_type=short("subscriptionType"),
    )


async def ask(hub, device_id: str) -> ClaudeLogin:
    """Ask the machine. Raises what ``hub.exec`` raises when it is not there."""
    result = await hub.exec(device_id, ["python3", "-"], stdin=program(), timeout=90)
    if result.get("exit") != 0:
        logger.warning(
            "claude login probe failed on %s: %s",
            device_id,
            str(result.get("stderr") or "")[-500:],
        )
        return ClaudeLogin(installed=False, logged_in=False)
    return read_answer(str(result.get("stdout") or ""))


async def remember(session: AsyncSession, device_id: str, login: ClaudeLogin) -> None:
    values = {
        "installed": login.installed,
        "logged_in": login.logged_in,
        "auth_method": login.auth_method,
        "subscription_type": login.subscription_type,
        "model": login.model,
        "service_host": login.service_host,
        "checked_at": datetime.now(UTC),
    }
    statement = insert(DeviceClaudeLoginRow).values(device_id=device_id, **values)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=["device_id"],
            set_={
                **values,
                # A login its owner removed stays out of use until the machine
                # has logged it out, whatever the machine says before then.
                "logged_in": case(
                    (
                        DeviceClaudeLoginRow.revoked_at.is_(None),
                        statement.excluded.logged_in,
                    ),
                    else_=False,
                ),
            },
        )
    )


async def status(session: AsyncSession, device_id: str) -> dict | None:
    """What the machine last said about its owner's Claude Code login, for its
    owner to read; None when it has not been asked yet."""
    row = await session.get(DeviceClaudeLoginRow, device_id, populate_existing=True)
    if row is None:
        return None
    return claude_login.view(row)


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


async def log_out(hub, device_id: str) -> None:
    """Log the platform's Claude Code out on the machine and forget its model
    service, as `cheesehost claude logout` does there. The owner's own Claude
    Code (``~/.claude``) is not touched."""
    await _run(hub, device_id, ["claude", "logout"], timeout=90)


async def settle(session: AsyncSession, hub, device_id: str) -> None:
    """Carry out a removal its owner made while the machine was away: log the
    machine out, then clear the removal. Raises what the machine raises,
    leaving the removal in place for its next connection."""
    pending = await session.scalar(
        select(DeviceClaudeLoginRow.revoked_at).where(
            DeviceClaudeLoginRow.device_id == device_id
        )
    )
    if pending is None:
        return
    await log_out(hub, device_id)
    await session.execute(
        update(DeviceClaudeLoginRow)
        .where(DeviceClaudeLoginRow.device_id == device_id)
        .values(revoked_at=None, logged_in=False, checked_at=datetime.now(UTC))
    )
    await session.commit()


async def revoke(session: AsyncSession, hub, device_id: str) -> None:
    """Its owner removes the machine's login, from wherever they are. It is out
    of use from this moment — no session starts with it — and the machine logs
    it out now if it is online, or as it next connects."""
    await session.execute(
        update(DeviceClaudeLoginRow)
        .where(DeviceClaudeLoginRow.device_id == device_id)
        .values(revoked_at=datetime.now(UTC), logged_in=False)
    )
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


async def use_model_service(
    session: AsyncSession, hub, device_id: str, *, base_url: str, token: str, model: str
) -> None:
    """Point the machine's own Claude Code at another model service, as
    `cheesehost claude login --base-url` does there. The key travels to the
    machine in that command's environment and is kept only there; nothing on
    this side stores or logs it. Raises ``MachineRefused`` with the
    connector's reason."""
    await settle(session, hub, device_id)
    await _run(
        hub,
        device_id,
        ["claude", "login", "--base-url", base_url, "--model", model],
        env={"CHEESE_MODEL_TOKEN": token},
        # The first use on a machine downloads the pinned Claude Code build.
        timeout=600,
    )
    await remember(session, device_id, await ask(hub, device_id))
    await session.commit()


async def refresh(session_factory, hub, device_id: str) -> ClaudeLogin:
    """Carry out a pending removal, then ask the machine and keep its answer."""
    async with session_factory() as session:
        try:
            await settle(session, hub, device_id)
        except Exception:
            logger.warning(
                "claude logout not carried out on %s", device_id, exc_info=True
            )
    login = await ask(hub, device_id)
    async with session_factory() as session:
        await remember(session, device_id, login)
        await session.commit()
    return login


async def refresh_on_connect(session_factory, hub, device_id: str) -> None:
    """Settle and ask a machine a person enrolled, as it connects. Cloud
    machines are the platform's and carry no one's login. A machine that goes
    again before it answers is asked on its next connection."""
    async with session_factory() as session:
        device = await session.get(DeviceRow, device_id)
        if device is None or device.supply != Supply.self_hosted:
            return
    try:
        await refresh(session_factory, hub, device_id)
    except Exception:
        logger.warning("claude login not checked on %s", device_id, exc_info=True)
