"""Where does a machine's model traffic actually go? Ask a real machine.

`microcloud_smoke.py` proves the MicroCloud contract by talking to MicroCloud
directly. It deliberately skips the part this script exists for: the PLATFORM
path — project -> machine row -> enrolment -> device -> turn — and, at the end
of it, the one question the accounting work hinges on:

    when Claude Code runs on that machine, whose endpoint does it call?

That question has a non-obvious failure mode. The screen is launched with
`bash -lc`, so MicroCloud's own login profile is sourced AFTER the environment
we hand the process. A profile line like `export ANTHROPIC_BASE_URL=<newapi>`
therefore silently overrides our gateway, and the spend lands on MicroCloud's
account with our books showing nothing. Reporting `ANTHROPIC_BASE_URL` as seen
by a LOGIN shell is what distinguishes "we set it" from "it survived".

Run inside the backend container on the box (it mints its own token and uses
the running server's API, same as `device_online_probe.py`):

    docker cp backend/scripts/machine_chain_check.py cheese-backend-1:/tmp/
    docker exec cheese-backend-1 python /tmp/machine_chain_check.py

It reports the project's existing machines; a room's agent opens its own.

Env: PROJECT_ID (explicit, wins) or PROJECT_NAME (default "cheese 自建"),
USER_HANDLE (default "andy"), BASE (default http://localhost:8081).
"""

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get("BASE", "http://localhost:8081").rstrip("/")
USER_HANDLE = os.environ.get("USER_HANDLE", "andy")
PROJECT_NAME = os.environ.get("PROJECT_NAME", "cheese 自建")


def log(message: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {message}", flush=True)


def call(method: str, path: str, token: str) -> dict:
    req = urllib.request.Request(
        f"{BASE}{path}",
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:400]
        raise RuntimeError(f"{method} {path} -> {exc.code}: {detail}") from exc


async def resolve(handle: str, project_name: str) -> tuple[str, str]:
    """(token, project_id). Reads the DB directly — the API has no lookup by name."""
    from sqlalchemy import select

    from app.common.auth import create_access_token
    from app.core.db import async_session_factory
    from app.domain.project.models import Project
    from app.domain.user.models import User

    async with async_session_factory() as session:
        user = (
            await session.execute(select(User).where(User.username == handle))
        ).scalar_one_or_none()
        if user is None:
            raise RuntimeError(f"no user {handle!r}")
        token = create_access_token(user.id, handle=user.username)

        explicit = os.environ.get("PROJECT_ID")
        if explicit:
            return token, str(uuid.UUID(explicit))
        project = (
            await session.execute(select(Project).where(Project.name == project_name))
        ).scalar_one_or_none()
        if project is None:
            names = [
                p.name
                for p in (await session.execute(select(Project).limit(20))).scalars()
            ]
            raise RuntimeError(f"no project named {project_name!r}; have: {names}")
        return token, str(project.id)


def describe(machine: dict) -> str:
    return (
        f"row={machine.get('id')} mc={machine.get('machine_id')} "
        f"{machine.get('hostname')} status={machine.get('status')} "
        f"ai={machine.get('ai_mode')}/{machine.get('ai_status')} "
        f"ip={machine.get('ip')} enrolled={bool(machine.get('device_id'))}"
    )


async def main() -> int:
    token, project_id = await resolve(USER_HANDLE, PROJECT_NAME)
    log(f"project {project_id}")

    # The list endpoint refreshes each row from MicroCloud, which is also how a
    # machine the provider has forgotten gets marked deleted instead of sitting
    # in our books as `running` forever.
    listed = call("GET", f"/projects/{project_id}/machines", token)
    machines = listed.get("data", {}).get("data", [])
    log(f"{len(machines)} machine row(s) after refresh")
    for m in machines:
        log(f"  {describe(m)}")

    alive = [m for m in machines if m.get("status") == "running"]
    if not alive:
        log("VERDICT: no running machine — the chain cannot be exercised")
        return 1

    for m in alive:
        log(f"RUNNING {describe(m)}")
    # Deliberately printed as the last line and machine-readable: the ssh probe
    # that answers the login-shell question runs from the box, not from here.
    print(
        "PROBE_TARGETS="
        + ",".join(f"{m.get('login_user')}@{m.get('ip')}" for m in alive if m.get("ip"))
    )
    return 0


try:
    raise SystemExit(asyncio.run(main()))
except RuntimeError as exc:
    log(f"FAILED: {exc}")
    raise SystemExit(2) from exc
