"""A conversation points to the place its work is in.

When nobody in a task works in the task's default environment — its only
agent works on its owner's computer — the task's machine is that agent's: a
person opening the task's files reads them there. Once an agent of the
default arrives, the task's machine is the default's, and the first agent
keeps working where it was.
"""

import hashlib
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.sandbox_auth import bind_resource_token, mint_scoped_token
from app.domain.agent import execution
from app.domain.agent.owner_provider import OWNER_CHANNEL
from app.domain.agent_session.services import AgentSessionService
from app.domain.environment import services as environment_rows
from app.domain.identity.services import IdentityService
from app.domain.machine import session_work
from app.domain.topic.models import Topic
from tests.integration.conftest import open_task, post_project, session_auth_headers
from tests.integration.test_agents_of_one_conversation_share_its_environment import (
    _computers,
)

pytestmark = pytest.mark.anyio


def _machines(monkeypatch) -> None:
    async def install(device, argv, **kwargs):
        return {
            "exit": 0,
            "stdout": json.dumps(
                {"state": f"/{device}/state", "workspace": "/work", "mcp_servers": []}
            ),
        }

    monkeypatch.setattr(
        session_work,
        "device_hub",
        SimpleNamespace(
            target=lambda _device: "linux-amd64",
            is_online=lambda device: True,
            reconnecting=lambda device: False,
            exec=AsyncMock(side_effect=install),
        ),
    )

    def executor_answer(target, method, *args, **_kwargs):
        if method == "ping":
            from app.domain.agent.harness.claude_code.remote_execution import (
                launch,
                runtime,
            )

            return {
                "capabilities": ["prepare"],
                "sandbox": False,
                "protocol_version": runtime.PROTOCOL_VERSION,
                "files": {
                    name: hashlib.sha256(value.encode()).hexdigest()
                    for name, value in launch.file_sources().items()
                },
            }
        if method == "prepare":
            return target
        return {}

    monkeypatch.setattr(execution, "call", AsyncMock(side_effect=executor_answer))


async def _seat(db, project_id, room_id, task_id, resource, handle, *, channel):
    session = await AgentSessionService(db).ensure(
        task_id, handle, harness="claude-code"
    )
    session.runtime_location = {
        "device_id": "center",
        "resource_id": resource,
        "channel": channel,
    }
    actor = (await IdentityService(db).ensure_room_agent_user(room_id)).username
    token = bind_resource_token(
        mint_scoped_token(
            project_id=str(project_id), topic_id=str(task_id), agent_handle=actor
        ),
        resource,
        session_id=str(session.id),
    )
    return session.id, token


def _hands(client, task_id, session_id, token) -> dict:
    response = client.post(
        f"/topics/{task_id}/sessions/{session_id}/work-lease",
        headers={"Authorization": f"Bearer {token}"},
        json={"env": {}},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["target"]


async def _task_machine(client, task_id, resource) -> str | None:
    async with client.test_factory() as db:
        found = await environment_rows.working_copy(db, task_id, resource)
    return found[0]["device_id"] if found is not None else None


async def _task_on_a_laptop(client, monkeypatch) -> SimpleNamespace:
    """A task of a room whose default is a shared computer, and its only
    agent, Ada, working on her own laptop."""
    project = post_project(client, json={"name": "Own hands"}, owner="alice").json()[
        "data"
    ]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    project_id, room_id = uuid.UUID(project["id"]), uuid.UUID(room["id"])
    task_id = uuid.UUID(open_task(client, room["id"], "Task", owner="alice")["id"])
    async with client.test_factory() as db:
        shared, laptop = await _computers(db, project_id, ("shared", "laptop"))
        topic = await db.get(Topic, room_id)
        topic.default_environment = {"kind": "self_hosted", "device_id": shared}
        resource = str(topic.resource_id or room_id)
        ada = await _seat(
            db, project_id, room_id, task_id, resource, "ada", channel=OWNER_CHANNEL
        )
        await AgentSessionService(db).set_environment(
            task_id, "ada", {"kind": "self_hosted", "device_id": laptop}, chosen_by=None
        )
        await db.commit()
    _machines(monkeypatch)
    assert _hands(client, task_id, *ada)["device_id"] == laptop
    return SimpleNamespace(
        project_id=project_id,
        room_id=room_id,
        task_id=task_id,
        resource=resource,
        shared=shared,
        laptop=laptop,
        ada=ada,
    )


async def test_a_task_worked_only_on_an_owners_computer_is_found_there(
    client, monkeypatch
):
    task = await _task_on_a_laptop(client, monkeypatch)

    # Nobody works in the default: the task's files are on Ada's laptop.
    assert await _task_machine(client, task.task_id, task.resource) == task.laptop


async def test_an_agent_of_the_default_takes_the_task_and_the_first_stays(
    client, monkeypatch
):
    task = await _task_on_a_laptop(client, monkeypatch)
    async with client.test_factory() as db:
        bob = await _seat(
            db,
            task.project_id,
            task.room_id,
            task.task_id,
            task.resource,
            "bob",
            channel="device",
        )
        await db.commit()
    assert _hands(client, task.task_id, *bob)["device_id"] == task.shared

    # Bob works in the default, so that is where the task's work is now;
    # Ada keeps working on her laptop.
    assert await _task_machine(client, task.task_id, task.resource) == task.shared
    assert _hands(client, task.task_id, *task.ada)["device_id"] == task.laptop
