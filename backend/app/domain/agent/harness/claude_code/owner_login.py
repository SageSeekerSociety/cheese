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
"""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.harness.claude_code.device_launch import CLAUDE_PINNED_VERSION
from app.domain.agent.place import CLAUDE_LOGIN_DIR, footprint_root
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
        f"VERSION = {CLAUDE_PINNED_VERSION!r}\n" + _PROGRAM
    )


@dataclass(frozen=True)
class ClaudeLogin:
    installed: bool
    logged_in: bool
    auth_method: str | None = None
    subscription_type: str | None = None


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
        "checked_at": datetime.now(UTC),
    }
    await session.execute(
        insert(DeviceClaudeLoginRow)
        .values(device_id=device_id, **values)
        .on_conflict_do_update(index_elements=["device_id"], set_=values)
    )


async def get(session: AsyncSession, device_id: str) -> DeviceClaudeLoginRow | None:
    return await session.get(DeviceClaudeLoginRow, device_id)


async def refresh(session_factory, hub, device_id: str) -> ClaudeLogin:
    """Ask the machine and keep its answer."""
    login = await ask(hub, device_id)
    async with session_factory() as session:
        await remember(session, device_id, login)
        await session.commit()
    return login


async def refresh_on_connect(session_factory, hub, device_id: str) -> None:
    """Ask a machine a person enrolled, as it connects. Cloud machines are the
    platform's and carry no one's login. A machine that goes again before it
    answers is asked on its next connection."""
    async with session_factory() as session:
        device = await session.get(DeviceRow, device_id)
        if device is None or device.supply != Supply.self_hosted:
            return
    try:
        await refresh(session_factory, hub, device_id)
    except Exception:
        logger.warning("claude login not checked on %s", device_id, exc_info=True)
