"""A 支线's session, and a task's before its owner starts it, keep nothing.

On a machine that is its own it reads, runs and changes files like any session:
that is how it finds out what is wrong. What it does there never reaches the
project: its sync is refused, the project's MCP servers stay out of reach, and
it is handed no credential that pushes. On a machine it shares with others it
only reads, because what it changed would stay among their work.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.sandbox_auth import bind_resource_token, mint_scoped_token
from app.domain.agent import execution
from app.domain.agent_session.services import AgentSessionService
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.topic.models import Topic
from app.domain.user.models import User
from tests.integration.conftest import (
    in_thread,
    open_task,
    post_project,
    room_agent_seat,
    session_auth_headers,
)

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
    if place == "thread":
        conversation = in_thread(client, room["id"], "alice")
    else:
        conversation = open_task(
            client, room["id"], "Task", owner="alice", start=place == "started"
        )["id"]
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
        )
        await devices.assign_to_project(
            device.device_id, project_id, actor_user_id=owner.id
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
    bound = {
        "session_id": session_id,
        "lease_generation": generation,
    }
    return {
        "conversation": conversation,
        "resource": resource,
        # What a turn of this session presents to the executor route.
        "turn": bind_resource_token(launch, resource, **bound, scratch=True),
        # What its executor's commands present to the platform.
        "machine": bind_resource_token(launch, resource, **bound),
    }


def _execute(client, monkeypatch, held: dict, method: str, params: dict):
    remote = AsyncMock(return_value={"done": True})
    monkeypatch.setattr(execution, "call", remote)
    response = client.post(
        f"/topics/{held['conversation']}/execution/session-{held['resource']}",
        headers={"X-Cheese-Token": held["turn"]},
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


@pytest.mark.parametrize("place", ["thread", "unstarted"])
@pytest.mark.parametrize("call", [BASH, SHELL, WRITE, EDIT, READ])
async def test_on_its_own_machine_it_reads_runs_and_changes_files(
    client, monkeypatch, place, call
):
    held = await _session(client, place)
    response, remote = _execute(client, monkeypatch, held, *call)
    assert response.status_code == 200, response.text
    remote.assert_awaited()


@pytest.mark.parametrize("place", ["thread", "unstarted"])
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


def _error_key(response) -> str | None:
    return ((response.json().get("error") or {}).get("i18n") or {}).get("key")


@pytest.mark.parametrize("place", ["thread", "unstarted"])
async def test_it_is_handed_no_credential_that_pushes(client, place):
    held = await _session(client, place)
    response = client.get(
        "/sandbox/forge-token", headers={"X-Cheese-Token": held["machine"]}
    )
    assert response.status_code == 403, response.text
    assert _error_key(response) == "forgeWorkNotKept"


async def test_a_started_task_is_not_refused_the_credential_for_this(client):
    held = await _session(client, "started")
    response = client.get(
        "/sandbox/forge-token", headers={"X-Cheese-Token": held["machine"]}
    )
    # This project has no repository bound, so no credential comes back; what
    # matters is that the refusal is not the one for work that is not kept.
    assert _error_key(response) != "forgeWorkNotKept", response.text
