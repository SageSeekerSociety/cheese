"""A project's local MCP servers — a command each, run on the room's machine.

The rules, stated before they were written:

- the settings page lists them beside the remote ones, with the command as
  written and the project secrets they name;
- a session's server is handed only the secrets its definition on the
  default branch names: not another server's, and never the header of a
  server added in settings;
- on a member's own computer a command runs only once the computer's owner
  allows it; until then the settings page says whose answer it waits for, and
  the question is in the owner's 「等你处理」;
- only that owner answers, and an answer is kept per command: an allowed one
  is what the session is told it may run, a refused one stays refused;
- the platform's own machines ask nobody.
"""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from app.core.sandbox_auth import mint_scoped_token
from app.domain.device.models import DeviceRow
from app.domain.device.supply import Supply
from app.domain.project.models import Project
from app.domain.user.repositories import UserRepository
from app.domain.user.tokens import digest_token
from tests.conftest import seed_user
from tests.integration.conftest import session_auth_headers
from tests.integration.test_remote_mcp import _local_upstream as _local_upstream
from tests.integration.test_remote_mcp import _project, _servers, _topic
from tests.integration.test_remote_mcp import upstream as upstream

NOTES = {
    "command": "uvx",
    "args": ["notes-mcp", "--token", "${NOTES_TOKEN}"],
    "env": {"NOTES_MODE": "${NOTES_MODE:-read}"},
}


@pytest.fixture
def room(client, upstream):
    """A project whose `.mcp.json` has a local server `notes`, with the secrets
    of every kind set: its own, a remote server's, and the header of a server
    added in settings."""
    pid = _project(client, upstream, extra={"notes": NOTES})
    alice = session_auth_headers("alice")
    for name, value in (
        ("NOTES_TOKEN", "notes-secret"),
        ("SEARCH_KEY", "search-secret"),
    ):
        put = client.put(
            f"/projects/{pid}/mcp/secrets/{name}", json={"value": value}, headers=alice
        )
        assert put.status_code == 200, put.text
    added = client.post(
        f"/projects/{pid}/mcp/servers",
        json={
            "name": "docs",
            "url": f"{upstream.base}/keyed",
            "auth": "headers",
            "header": "X-Api-Key",
            "value": "docs-header-secret",
        },
        headers=alice,
    )
    assert added.status_code == 200, added.text
    tid = _topic(client, pid)
    return (
        pid,
        tid,
        {"x-cheese-token": mint_scoped_token(project_id=str(pid), topic_id=tid)},
    )


def _computer(client, pid, owner: str, *, supply=Supply.self_hosted) -> str:
    """A computer the project may run on, owned by ``owner``."""
    seed_user(client, owner)
    device_id = f"dev-{uuid.uuid4().hex[:8]}"

    async def enrol():
        async with client.test_factory() as session:
            user = await UserRepository(session).get_by_username(owner)
            assert user is not None
            session.add(
                DeviceRow(
                    device_id=device_id,
                    name=f"{owner} 的笔记本",
                    token_digest=digest_token(f"tok-{device_id}"),
                    team_id=(await session.get(Project, pid)).team_id,
                    owner_user_id=user.id,
                    created_at=datetime.now(UTC),
                    supply=supply,
                )
            )
            await session.flush()
            await session.commit()

    asyncio.run(enrol())
    return device_id


def _start(client, tid, session, device_id=None) -> dict:
    response = client.post(
        f"/topics/{tid}/mcp/notes/start", json={"device_id": device_id}, headers=session
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _report(client, tid, session, **body) -> dict:
    response = client.post(f"/topics/{tid}/mcp/notes/run", json=body, headers=session)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _waiting(client, handle) -> list[dict]:
    response = client.get(
        "/awaiting-me", headers={"Authorization": f"Bearer {seed_user(client, handle)}"}
    )
    assert response.status_code == 200, response.text
    return [i for i in response.json()["data"]["data"] if i["kind"] == "mcp_run"]


def _decide(client, approval_id, handle, allow) -> int:
    return client.post(
        f"/mcp/approvals/{approval_id}",
        json={"allow": allow},
        headers={"Authorization": f"Bearer {seed_user(client, handle)}"},
    ).status_code


def test_the_settings_page_lists_a_local_server_with_its_command(client, room):
    pid, _, _ = room

    notes = _servers(client, pid)["notes"]

    assert notes["transport"] == "stdio"
    assert notes["command"] == "uvx notes-mcp --token '${NOTES_TOKEN}'"
    assert notes["status"] == "ready"
    assert [v["name"] for v in notes["variables"]] == ["NOTES_TOKEN", "NOTES_MODE"]
    assert notes["run"]["state"] == "idle"
    assert "notes-secret" not in str(notes)


def test_a_server_is_handed_only_the_secrets_its_own_definition_names(client, room):
    _, tid, session = room

    started = _start(client, tid, session)

    assert started["values"] == {"NOTES_TOKEN": "notes-secret"}
    assert started["allowed"] is None, "nobody is asked on the platform's machines"


def test_a_name_the_default_branch_does_not_declare_gets_nothing(client, room):
    _, tid, session = room

    response = client.post(f"/topics/{tid}/mcp/search/start", json={}, headers=session)

    assert response.status_code == 200, response.text
    assert response.json()["data"]["values"] == {}


def test_a_command_waits_for_the_devices_team_admin(client, room):
    pid, tid, session = room
    device = _computer(client, pid, "dana")

    assert _start(client, tid, session, device)["allowed"] == []
    said = _report(
        client,
        tid,
        session,
        state="awaiting",
        device_id=device,
        fingerprint="f" * 64,
        command="uvx notes-mcp --token '${NOTES_TOKEN}'",
    )
    assert said["state"] == "awaiting"

    run = _servers(client, pid)["notes"]["run"]
    assert (run["state"], run["owner"]) == ("awaiting", "alice")
    [asked] = _waiting(client, "alice")
    assert asked["server"] == "notes"
    assert asked["device"] == "dana 的笔记本"
    assert asked["command"] == "uvx notes-mcp --token '${NOTES_TOKEN}'"
    assert _waiting(client, "dana") == []

    assert _decide(client, asked["id"], "dana", True) == 403
    assert _decide(client, asked["id"], "alice", True) == 200
    assert _start(client, tid, session, device)["allowed"] == ["f" * 64]
    assert _servers(client, pid)["notes"]["run"]["state"] == "idle"
    assert _waiting(client, "dana") == []


def test_a_refused_command_stays_refused(client, room):
    pid, tid, session = room
    device = _computer(client, pid, "dana")
    ask = {
        "state": "awaiting",
        "device_id": device,
        "fingerprint": "e" * 64,
        "command": "uvx notes-mcp",
    }
    _report(client, tid, session, **ask)
    [asked] = _waiting(client, "alice")

    assert _decide(client, asked["id"], "alice", False) == 200

    assert _report(client, tid, session, **ask)["state"] == "refused"
    assert _waiting(client, "dana") == [], "the same command is not asked again"
    assert _start(client, tid, session, device)["allowed"] == []
    assert _servers(client, pid)["notes"]["run"]["state"] == "refused"


def test_a_failed_start_keeps_the_end_of_what_it_printed(client, room):
    pid, tid, session = room

    _report(
        client,
        tid,
        session,
        state="failed",
        log=[f"line {n}" for n in range(30)] + ["ModuleNotFoundError: notes"],
    )

    run = _servers(client, pid)["notes"]["run"]
    assert run["state"] == "failed"
    assert run["log"][-1] == "ModuleNotFoundError: notes"
    assert len(run["log"]) <= 12
