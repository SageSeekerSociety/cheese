"""A 支线's session keeps nothing; a task's keeps its work.

On a machine that is its own it reads, runs and changes files like any session:
that is how it finds out what is wrong. What it does there never reaches the
project: its sync is refused, the project's MCP servers stay out of reach, and
the forge token it is handed reads and never pushes. On a machine it shares
with others it only reads, because what it changed would stay among their
work.

The place decides that, not the credential: one signed while the session was
in a 支线 goes on saying so until the session goes quiet, and once the 支线
became a task (``place="became"``) the session works like any task's
(``place="task"``).
"""

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select

from app.core.sandbox_auth import bind_resource_token, mint_scoped_token
from app.domain.agent import execution
from app.domain.agent_session.services import AgentSessionService
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.project.github_app import GitHubAppTokens
from app.domain.topic.models import Topic
from app.domain.user.models import User
from tests.integration.conftest import (
    in_thread,
    open_task,
    post_project,
    room_agent_seat,
    session_auth_headers,
)
from tests.support.devices import enrollment_team, place_in_project_team

pytestmark = pytest.mark.anyio


async def _session(client, place: str, *, own: bool | None = True) -> dict:
    """An agent session in a 支线 (``place="thread"``) or a task, holding a
    machine of the project's; ``own`` is what its lease says about the
    machine, None for a lease recorded before it said anything."""
    project = post_project(client, json={"name": "Scratch"}, owner="alice").json()[
        "data"
    ]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    if place in ("thread", "became"):
        conversation = in_thread(client, room["id"], "alice")
        if place == "became":
            root = client.get(
                f"/topics/{conversation}/thread", headers=session_auth_headers("alice")
            ).json()["data"]["root_block_id"]
            made = client.post(
                f"/blocks/{root}/upgrade", headers=session_auth_headers("alice")
            )
            assert made.json()["data"]["id"] == conversation, made.text
    else:
        conversation = open_task(client, room["id"], "Task", owner="alice")["id"]
    project_id, room_id = uuid.UUID(project["id"]), uuid.UUID(room["id"])
    seat = room_agent_seat(client, room_id)
    async with client.test_factory() as db:
        owner = await db.scalar(select(User).where(User.username == "alice"))
        if owner is None:
            owner = User(
                username="alice",
                email="alice@example.test",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            db.add(owner)
            await db.flush()
        devices = sql_device_service(db)
        device = await devices.approve(
            await devices.start("ada"),
            owner_user_id=owner.id,
            supply=Supply.self_hosted,
            team_id=await enrollment_team(devices, owner.id),
        )
        await place_in_project_team(
            devices, device.device_id, project_id, actor_user_id=owner.id
        )
        topic = await db.get(Topic, room_id)
        assert topic is not None
        resource = str(topic.resource_id or room_id)
        session = await AgentSessionService(db).ensure(
            uuid.UUID(conversation), seat, harness="claude-code"
        )
        session.runtime_location = {
            "device_id": "center",
            "resource_id": resource,
            "channel": "device",
        }
        generation = str(uuid.uuid4())
        session.execution_request = {"generation": generation}
        session.work_lease = {
            "kind": "device",
            "status": "ready",
            "session_id": str(session.id),
            "generation": generation,
            "resource_id": resource,
            "room_resource_id": resource,
            "device_id": device.device_id,
            "workspace": "/work",
            "mcp_servers": [],
            "url": f"http://api/topics/{conversation}/execution/session-{resource}",
            **({} if own is None else {"own": own}),
        }
        session_id = str(session.id)
        await db.commit()
    launch = mint_scoped_token(
        project_id=str(project_id), topic_id=conversation, agent_handle=seat
    )
    return {
        "conversation": conversation,
        "room": room["id"],
        "owner": owner.id,
        "session": session_id,
        "device": device.device_id,
        "resource": resource,
        # What a turn of this session presents to the executor route.
        "turn": bind_resource_token(
            launch,
            resource,
            session_id=session_id,
            lease_generation=generation,
            scratch=True,
        ),
        # What its executor's commands present to the platform.
        "machine": bind_resource_token(
            launch, resource, session_id=session_id, lease_generation=generation
        ),
    }


def _execute(client, monkeypatch, held: dict, method: str, params: dict):
    remote = AsyncMock(return_value={"done": True})
    monkeypatch.setattr(execution, "call", remote)
    response = client.post(
        f"/topics/{held['conversation']}/execution/session-{held['resource']}",
        headers={"Authorization": f"Bearer {held['turn']}"},
        json={"method": method, "params": params},
    )
    return response, remote


BASH = ("invoke", {"id": "1", "tool": "Bash", "args": {"command": "pytest -x"}})
SHELL = ("control", {"subtype": "shell", "operation": "start", "body": "ls"})
WRITE = ("control", {"subtype": "files", "operation": "write", "path": "a"})
EDIT = ("invoke", {"id": "2", "tool": "Edit", "args": {"file_path": "a"}})
READ = ("invoke", {"id": "3", "tool": "Read", "args": {"file_path": "README.md"}})
FILE_READ = ("control", {"subtype": "files", "operation": "read", "path": "a"})
SYNC = ("control", {"subtype": "checkpoint", "request_id": "checkpoint-1"})
MCP = ("mcp", {"server": "tracker", "method": "tools/call"})
MCP_TOOL = ("invoke", {"id": "4", "tool": "create_issue", "server": "tracker"})


@pytest.mark.parametrize("place", ["thread"])
@pytest.mark.parametrize("call", [BASH, SHELL, WRITE, EDIT, READ])
async def test_on_its_own_machine_it_reads_runs_and_changes_files(
    client, monkeypatch, place, call
):
    held = await _session(client, place)
    response, remote = _execute(client, monkeypatch, held, *call)
    assert response.status_code == 200, response.text
    remote.assert_awaited()


@pytest.mark.parametrize("place", ["thread"])
@pytest.mark.parametrize("call", [SYNC, MCP, MCP_TOOL])
async def test_nothing_it_does_is_carried_off_the_machine(
    client, monkeypatch, place, call
):
    held = await _session(client, place)
    response, remote = _execute(client, monkeypatch, held, *call)
    assert response.status_code == 403, response.text
    remote.assert_not_awaited()


@pytest.mark.parametrize("own", [False, None])
@pytest.mark.parametrize(
    ("call", "admitted"),
    [(READ, True), (FILE_READ, True), (BASH, False), (SHELL, False), (WRITE, False)],
)
async def test_on_a_shared_machine_it_only_reads(
    client, monkeypatch, own, call, admitted
):
    held = await _session(client, "thread", own=own)
    response, remote = _execute(client, monkeypatch, held, *call)
    if admitted:
        assert response.status_code == 200, response.text
    else:
        assert response.status_code == 403, response.text
        remote.assert_not_awaited()


@pytest.mark.parametrize("place", ["task", "became"])
@pytest.mark.parametrize("own", [True, False, None])
@pytest.mark.parametrize("call", [BASH, SHELL, WRITE, EDIT, READ])
async def test_a_task_works_though_its_credential_says_scratch(
    client, monkeypatch, place, own, call
):
    """A credential is signed when the session starts and replaced only once
    the session goes quiet (`screen_identity`): a 支线 that becomes a task
    mid-turn goes on presenting one that says its work is not kept. The place
    decides, so on a machine it shares it works all the same."""
    held = await _session(client, place, own=own)
    response, remote = _execute(client, monkeypatch, held, *call)
    assert response.status_code == 200, response.text
    remote.assert_awaited()


@pytest.mark.parametrize("place", ["task", "became"])
@pytest.mark.parametrize("call", [SYNC, MCP, MCP_TOOL])
async def test_a_task_carries_its_work_off_though_it_says_scratch(
    client, monkeypatch, place, call
):
    held = await _session(client, place)
    response, remote = _execute(client, monkeypatch, held, *call)
    assert response.status_code == 200, response.text
    remote.assert_awaited()


class _Forge:
    """A forge that tells which of its tokens it handed out."""

    async def installation_token(self):
        return "pushes", "2026-10-07T12:00:00+00:00"

    async def granted_permissions(self):
        return {"contents": "write"}

    async def read_token(self):
        return "reads", "2026-10-07T12:00:00+00:00"

    async def read_permissions(self):
        return {"contents": "read"}


def _forge_token(client, monkeypatch, held: dict) -> dict:
    binding = SimpleNamespace(
        kind="github_app",
        repo="acme/app",
        url="https://github.com/acme/app.git",
        api_url="https://api.github.com",
        default_branch="main",
    )
    monkeypatch.setattr(
        "app.domain.project.forge.binding_for_project",
        AsyncMock(return_value=binding),
    )
    monkeypatch.setattr(
        "app.domain.project.forge.tokens_for_project",
        AsyncMock(return_value=_Forge()),
    )
    response = client.get(
        "/sandbox/forge-token", headers={"Authorization": f"Bearer {held['machine']}"}
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


@pytest.mark.parametrize("place", ["thread"])
async def test_its_forge_token_reads_and_never_pushes(client, monkeypatch, place):
    held = await _session(client, place)
    data = _forge_token(client, monkeypatch, held)
    assert data["token"] == "reads"
    assert data["read_only"] is True


@pytest.mark.parametrize("place", ["task", "became"])
async def test_a_task_gets_the_token_that_pushes(client, monkeypatch, place):
    held = await _session(client, place)
    data = _forge_token(client, monkeypatch, held)
    assert data["token"] == "pushes"
    assert data["read_only"] is False


def _github_minted(client, monkeypatch, tmp_path, held: dict) -> tuple[dict, dict]:
    """The forge token this session is handed by a real minter, and the
    permissions that minter asked GitHub for. The installation holds
    `actions: write` alongside the grants an agent pushes with."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_path = tmp_path / "app.pem"
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    granted = {
        "actions": "write",
        "checks": "read",
        "contents": "write",
        "metadata": "read",
        "pull_requests": "write",
        "workflows": "write",
    }
    asked: list[dict] = []

    def github(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"permissions": granted})
        asked.append(json.loads(request.content)["permissions"])
        return httpx.Response(
            201,
            json={"token": "ghs_minted", "expires_at": "2026-10-07T12:00:00Z"},
        )

    minter = GitHubAppTokens(
        app_id=1,
        private_key_path=str(key_path),
        installation_id=2,
        transport=httpx.MockTransport(github),
    )
    binding = SimpleNamespace(
        kind="github_app",
        repo="acme/app",
        url="https://github.com/acme/app.git",
        api_url="https://api.github.com",
        default_branch="main",
    )
    monkeypatch.setattr(
        "app.domain.project.forge.binding_for_project",
        AsyncMock(return_value=binding),
    )
    monkeypatch.setattr(
        "app.domain.project.forge.tokens_for_project",
        AsyncMock(return_value=minter),
    )
    response = client.get(
        "/sandbox/forge-token", headers={"Authorization": f"Bearer {held['machine']}"}
    )
    assert response.status_code == 200, response.text
    [permissions] = asked
    return response.json()["data"], permissions


@pytest.mark.parametrize("place", ["thread"])
async def test_its_github_token_may_rerun_ci_and_still_never_pushes(
    client, monkeypatch, tmp_path, place
):
    """A routine run in a 支线 that watches a deployment reruns a flaky CI job
    itself: the repository stays read-only to it, CI does not."""
    held = await _session(client, place)
    data, asked = _github_minted(client, monkeypatch, tmp_path, held)
    assert asked == {
        "actions": "write",
        "checks": "read",
        "contents": "read",
        "metadata": "read",
        "pull_requests": "read",
    }
    assert "actions: write" in data["permissions"]
    assert "contents: read" in data["permissions"]
    assert data["read_only"] is True


@pytest.mark.parametrize("place", ["task"])
async def test_a_tasks_github_token_carries_every_grant(
    client, monkeypatch, tmp_path, place
):
    held = await _session(client, place)
    data, asked = _github_minted(client, monkeypatch, tmp_path, held)
    assert asked["contents"] == "write"
    assert asked["actions"] == "write"
    assert asked["workflows"] == "write"
    assert data["read_only"] is False


async def _move(client, monkeypatch, held: dict) -> dict:
    """The session moves itself into a sandbox on its owner's computers."""
    monkeypatch.setattr(execution, "call", AsyncMock(return_value={}))
    response = client.put(
        f"/topics/{held['conversation']}/sessions/{held['session']}/environment",
        headers={"Authorization": f"Bearer {held['turn']}"},
        json={"environment": {"kind": "sandbox", "spec": "team_devices"}},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


@pytest.mark.parametrize("place", ["thread"])
async def test_a_session_that_keeps_nothing_is_told_so_when_it_moves(
    client, monkeypatch, place
):
    """Moving does not let it keep its work: the answer to the move says so,
    rather than its first push being refused and sending it to look for an
    environment that would take it."""
    held = await _session(client, place)
    moved = await _move(client, monkeypatch, held)
    assert moved["keeps_nothing"] is True


@pytest.mark.parametrize("place", ["task", "became"])
async def test_a_task_that_moves_is_not_told_it_keeps_nothing(
    client, monkeypatch, place
):
    held = await _session(client, place)
    moved = await _move(client, monkeypatch, held)
    assert moved["keeps_nothing"] is False
