"""Each agent in a conversation has its own execution environment (#2320).

The rules, as a person would state them before any of this was built:

- changing one agent's environment moves that agent and nobody else;
- a room's (or a task's) default decides only what an agent that has not yet
  needed an environment gets: changing it moves nobody;
- a task's agent takes the default it finds the first time it needs one and
  keeps it when the room's default changes later;
- an agent changes only its own environment, and only to a sandbox; running
  directly on a computer, without a sandbox, is that computer's owner's choice;
- a sandbox goes where its spec says: the platform's spec never lands on a
  person's computer, and one person's spec never on another's or the
  platform's;
- a change of harness keeps the agent's environment;
- nobody is shown the platform's machines.
"""

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.common.auth import create_access_token
from app.core.config import settings
from app.core.sandbox_auth import bind_resource_token, mint_scoped_token
from app.domain.agent_session.services import AgentSessionService
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.machine import session_work as work_lease
from app.domain.machine.services import HostPool
from app.domain.project.models import Project
from app.domain.team.models import TeamMemberRole, TeamUserRelation
from app.domain.topic.models import Topic
from app.domain.user.models import User
from tests.integration.conftest import registered
from tests.integration.test_switch_pushes_first import (
    _environment,
    _machines,
    _room,
)
from tests.support.devices import enrollment_team, place_in_project_team

pytestmark = pytest.mark.anyio

STANDARD = {"kind": "sandbox", "spec": "standard"}


def _self_hosted(device_id: str) -> dict:
    return {"kind": "self_hosted", "device_id": device_id}


async def _second_agent(client, room, handle="linus", *, environment=None):
    """Another agent session in the same room, on the old machine like the
    first, with its own credential."""
    async with client.test_factory() as db:
        topic = await db.get(Topic, room.topic_id)
        resource = str(topic.resource_id or room.topic_id)
        sessions = AgentSessionService(db)
        row = await sessions.ensure(room.conversation_id, handle, harness="claude-code")
        generation = str(uuid.uuid4())
        row.execution_request = {"generation": generation}
        row.runtime_location = {
            "device_id": "center",
            "resource_id": resource,
            "channel": "device",
        }
        if environment is not None:
            await sessions.set_environment(
                room.conversation_id, handle, environment, chosen_by=None
            )
            row.work_lease = {
                **room.lease,
                "session_id": str(row.id),
                "generation": generation,
                "resource_id": str(uuid.uuid4()),
            }
        token = bind_resource_token(
            # Seated as the room's agent; the session is keyed by ``handle``.
            mint_scoped_token(
                project_id=str(room.project_id),
                topic_id=str(room.conversation_id),
                agent_handle=room.agent_handle,
            ),
            resource,
            session_id=str(row.id),
        )
        await db.commit()
        return SimpleNamespace(
            session_id=row.id,
            handle=handle,
            agent={"Authorization": f"Bearer {token}"},
            path=f"/topics/{room.conversation_id}/sessions/{row.id}/environment",
        )


async def _stored(client, conversation_id, handle):
    async with client.test_factory() as db:
        chosen = await AgentSessionService(db).environment(conversation_id, handle)
        return chosen.environment if chosen else None


async def _member(client, room, handle="bob") -> dict:
    """A person on the project's team who owns nothing there."""
    async with client.test_factory() as db:
        user_id = await registered(db, handle)
        team_id = (await db.get(Project, room.project_id)).team_id
        now = datetime.now(UTC)
        db.add(
            TeamUserRelation(
                team_id=team_id,
                user_id=user_id,
                role=TeamMemberRole.MEMBER,
                created_at=now,
                updated_at=now,
            )
        )
        await db.commit()
    return {"Authorization": "Bearer " + create_access_token(user_id, handle=handle)}


async def test_switching_one_agent_leaves_another_where_it_is(client, monkeypatch):
    room = await _room(client)
    other = await _second_agent(client, room, environment=_self_hosted(room.old_device))
    _machines(monkeypatch)

    switched = client.put(
        room.path,
        headers=room.person,
        json={"environment": _self_hosted(room.new_device)},
    )

    assert switched.status_code == 200, switched.text
    assert (await _environment(client, room))["device_id"] == room.new_device
    assert await _stored(client, room.conversation_id, other.handle) == _self_hosted(
        room.old_device
    )
    async with client.test_factory() as db:
        untouched = await AgentSessionService(db).by_id(other.session_id)
    assert untouched.work_lease["device_id"] == room.old_device


async def test_a_default_moves_nobody_and_a_new_agent_takes_it(client, monkeypatch):
    room = await _room(client)
    newcomer = await _second_agent(client, room)
    _machines(monkeypatch, online=False)

    changed = client.put(
        f"/topics/{room.topic_id}/environment-default",
        headers=room.person,
        json={"environment": _self_hosted(room.new_device)},
    )

    assert changed.status_code == 200, changed.text
    # The agent that already has an environment keeps it.
    assert (await _environment(client, room))["device_id"] == room.old_device
    shown = client.get(
        f"/topics/{room.topic_id}/environment", headers=room.person
    ).json()["data"]
    assert (shown["default"], shown["default_from"]) == (
        _self_hosted(room.new_device),
        "room",
    )
    # The newcomer had none: the first time it needs hands it takes the default.
    asked = client.post(
        f"/topics/{room.conversation_id}/sessions/{newcomer.session_id}/work-lease",
        headers=newcomer.agent,
        json={"env": {}, "timeout": 0.01},
    )
    assert asked.status_code == 200, asked.text
    assert await _stored(client, room.conversation_id, newcomer.handle) == _self_hosted(
        room.new_device
    )


async def test_a_tasks_agent_keeps_the_default_it_started_with(client, monkeypatch):
    room = await _room(client, in_task=True)
    newcomer = await _second_agent(client, room)
    _machines(monkeypatch, online=False)
    first = client.put(
        f"/topics/{room.topic_id}/environment-default",
        headers=room.person,
        json={"environment": _self_hosted(room.new_device)},
    )
    assert first.status_code == 200, first.text
    task = client.get(
        f"/topics/{room.conversation_id}/environment", headers=room.person
    ).json()["data"]
    assert (task["default"], task["default_from"]) == (None, "room")
    client.post(
        f"/topics/{room.conversation_id}/sessions/{newcomer.session_id}/work-lease",
        headers=newcomer.agent,
        json={"env": {}, "timeout": 0.01},
    )

    later = client.put(
        f"/topics/{room.topic_id}/environment-default",
        headers=room.person,
        json={"environment": _self_hosted(room.old_device)},
    )

    assert later.status_code == 200, later.text
    assert await _stored(client, room.conversation_id, newcomer.handle) == _self_hosted(
        room.new_device
    )


async def test_an_agent_changes_only_its_own_environment_and_only_to_a_sandbox(
    client, monkeypatch
):
    room = await _room(client)
    other = await _second_agent(client, room)
    _machines(monkeypatch)
    mine = {"kind": "sandbox", "spec": "team_devices"}

    on_another = client.put(other.path, headers=room.agent, json={"environment": mine})
    unisolated = client.put(
        room.path,
        headers=room.agent,
        json={"environment": _self_hosted(room.new_device)},
    )
    default = client.put(
        f"/topics/{room.topic_id}/environment-default",
        headers=room.agent,
        json={"environment": mine},
    )
    own = client.put(room.path, headers=room.agent, json={"environment": mine})

    assert on_another.status_code == 403, on_another.text
    assert unisolated.status_code == 403, unisolated.text
    assert default.status_code == 403, default.text
    assert own.status_code == 200, own.text
    assert await _environment(client, room) == mine


async def test_only_a_computers_owner_runs_an_agent_on_it_unisolated(
    client, monkeypatch
):
    room = await _room(client)
    bob = await _member(client, room)
    _machines(monkeypatch)
    there = {"environment": _self_hosted(room.new_device)}

    by_bob = client.put(room.path, headers=bob, json=there)
    by_owner = client.put(room.path, headers=room.person, json=there)

    assert by_bob.status_code == 403, by_bob.text
    assert by_owner.status_code == 200, by_owner.text


async def test_team_computers_are_offered_while_one_can_make_a_sandbox(client):
    room = await _room(client)
    path = f"/topics/{room.topic_id}/environment"

    def offered():
        data = client.get(path, headers=room.person).json()["data"]
        return {spec["spec"]: spec["label"] for spec in data["specs"]}

    assert offered()["team_devices"] == "团队设备"
    async with client.test_factory() as db:
        devices = sql_device_service(db)
        for device in (room.old_device, room.new_device):
            await devices.record_sandbox_check(device, isolates=False, reason=None)
        await db.commit()

    assert "team_devices" not in offered()
    refused = client.put(
        f"/topics/{room.topic_id}/environment-default",
        headers=room.person,
        json={"environment": {"kind": "sandbox", "spec": "team_devices"}},
    )
    assert refused.status_code in (400, 422), refused.text


async def test_the_platforms_sandbox_never_lands_on_a_persons_computer(
    client, monkeypatch
):
    room = await _room(client)
    monkeypatch.setattr(settings, "microcloud_base_url", "https://example.invalid")
    monkeypatch.setattr(settings, "microcloud_tenant_secret", "test-only")
    _machines(monkeypatch)
    work_lease.device_hub.exec = AsyncMock(
        return_value={
            "exit": 0,
            "stdout": json.dumps(
                {"state": "/state", "workspace": "/work", "mcp_servers": []}
            ),
        }
    )
    placed = SimpleNamespace(
        id=uuid.uuid4(), device_id="platform-host", whole_machine=False
    )

    async def place(self, session_id, **kwargs):
        return placed

    monkeypatch.setattr(HostPool, "place", place)
    monkeypatch.setattr(HostPool, "tell_ready", lambda *a, **k: _none())
    switched = client.put(
        room.path, headers=room.person, json={"environment": STANDARD}
    )
    assert switched.status_code == 200, switched.text

    leased = client.post(room.lease_path, headers=room.agent, json={"env": {}})

    assert leased.status_code == 200, leased.text
    target = leased.json()["data"]["target"]
    assert target["device_id"] == "platform-host"
    assert target["platform"] is True


async def _none():
    return None


async def test_a_team_sandbox_never_lands_on_another_teams_computer(
    client, monkeypatch
):
    room = await _room(client)
    async with client.test_factory() as db:
        bob = await registered(db, "bob")
        devices = sql_device_service(db)
        await devices.approve(
            await devices.start("bob's"),
            owner_user_id=bob,
            supply=Supply.self_hosted,
            team_id=await enrollment_team(devices, bob),
        )
        # Alice's machines both said they cannot make a sandbox.
        for device in (room.old_device, room.new_device):
            await devices.record_sandbox_check(device, isolates=False, reason=None)
        await AgentSessionService(db).set_environment(
            room.conversation_id,
            room.agent_handle,
            {"kind": "sandbox", "spec": "team_devices"},
            chosen_by=None,
        )
        session = await AgentSessionService(db).by_id(room.session_id)
        session.work_lease = None
        await db.commit()
    _machines(monkeypatch)
    monkeypatch.setattr(
        work_lease.device_hub,
        "exec",
        lambda *a, **k: pytest.fail("installed"),
        raising=False,
    )

    leased = client.post(room.lease_path, headers=room.agent, json={"env": {}})

    assert leased.status_code == 200, leased.text
    assert "unavailable" in leased.json()["data"]


async def test_a_change_of_harness_keeps_the_agents_environment(client):
    room = await _room(client)
    from app.domain.machine.environments import environment_of

    async with client.test_factory() as db:
        sessions = AgentSessionService(db)
        topic = await db.get(Topic, room.topic_id)
        topic.default_environment = STANDARD
        other_harness = await sessions.ensure(
            room.conversation_id, room.agent_handle, harness="pi"
        )
        project = await db.get(Project, room.project_id)
        kept = await environment_of(db, other_harness, topic, None, project)
        await db.commit()

    assert kept.model_dump() == _self_hosted(room.old_device)


async def test_nobody_is_shown_the_platforms_machines(client):
    room = await _room(client)
    async with client.test_factory() as db:
        owner = await db.scalar(select(User).where(User.username == "alice"))
        devices = sql_device_service(db)
        host = await devices.approve(
            await devices.start("pool host"),
            owner_user_id=owner.id,
            supply=Supply.cloud,
            team_id=await enrollment_team(devices, owner.id),
        )
        await place_in_project_team(
            devices, host.device_id, room.project_id, actor_user_id=owner.id
        )
        await db.commit()

    shown = client.get(f"/topics/{room.topic_id}/environment", headers=room.person)

    assert shown.status_code == 200, shown.text
    listed = {device["device_id"] for device in shown.json()["data"]["devices"]}
    assert listed == {room.old_device, room.new_device}
    assert host.device_id not in shown.text


async def test_device_sessions_select_count_does_not_scale_with_sessions(client):
    room = await _room(client)
    from sqlalchemy import event

    from app.domain.machine.session_reports import device_sessions
    from app.domain.room_task.models import Task

    async def add_task_sessions(titles: list[str]) -> None:
        async with client.test_factory() as db:
            sessions = AgentSessionService(db)
            for title in titles:
                task = Task(
                    project_id=room.project_id,
                    room_id=room.topic_id,
                    title=title,
                )
                db.add(task)
                await db.flush()
                await sessions.ensure(task.id, room.agent_handle, harness="claude-code")
                await sessions.set_environment(
                    task.id,
                    room.agent_handle,
                    _self_hosted(room.old_device),
                    chosen_by=None,
                )
            await db.commit()

    async def count_selects() -> tuple[int, list[dict]]:
        selects = 0

        def record(_conn, _cursor, statement: str, *_rest) -> None:
            nonlocal selects
            if statement.lstrip().upper().startswith("SELECT"):
                selects += 1

        async with client.test_factory() as db:
            target = db.sync_session.bind
            event.listen(target, "before_cursor_execute", record)
            try:
                rows = await device_sessions(db, room.project_id, room.old_device)
            finally:
                event.remove(target, "before_cursor_execute", record)
        return selects, [session for _topic, session in rows]

    await add_task_sessions([f"Task {i}" for i in range(2)])
    selects_3, sessions_3 = await count_selects()

    await add_task_sessions([f"Task {i}" for i in range(2, 11)])
    selects_12, sessions_12 = await count_selects()

    assert len(sessions_3) == 3
    assert len(sessions_12) == 12
    assert {s["task_title"] for s in sessions_3} == {None, "Task 0", "Task 1"}
    assert all(s["agent_name"] for s in sessions_12)
    assert selects_3 > 0
    assert selects_3 == selects_12
