"""A task's work computer: its own choice, changed by its owner.

The rules a person could state before any of this was built:

- a task takes its room's computer the first time it needs one, unless its
  owner picks another, and keeps what it has when the room changes;
- only the task's owner (or the task's own AI session) changes it;
- a computer shared with the project's team works anyone's tasks; a person's
  own computer works only the tasks that person owns.
"""

import uuid

from app.core.sandbox_auth import mint_scoped_token
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.project.models import Project
from app.domain.user.repositories import UserRepository
from tests.integration.conftest import (
    join_project_team,
    open_task,
    post_project,
    room_agent_seat,
    session_auth_headers,
)


def _room(client) -> tuple[str, str]:
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "房间"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    join_project_team(client, project["id"], "bob")
    return project["id"], room["id"]


def _computer(client, project_id: str, name: str, *, owner: str, team: bool) -> str:
    """A computer ``owner`` enrolled: shared with the project's team, or only
    put in this project."""

    async def _enrol() -> str:
        async with client.test_request_factory() as session:
            user = await UserRepository(session).get_by_username(owner)
            assert user is not None
            devices = sql_device_service(session)
            device = await devices.approve(
                await devices.start(name),
                owner_user_id=user.id,
                supply=Supply.self_hosted,
            )
            if team:
                project = await session.get(Project, uuid.UUID(project_id))
                await devices.assign_to_team(
                    device.device_id, project.team_id, actor_user_id=user.id
                )
            else:
                await devices.assign_to_project(
                    device.device_id, uuid.UUID(project_id), actor_user_id=user.id
                )
            await session.commit()
            return device.device_id

    return client.portal.call(_enrol)


def _pick(client, room_id, device_id, *, task_id=None, headers=None):
    """Pick a computer for the room, or for the task when ``task_id`` names
    one: either is addressed by its own conversation id."""
    return client.put(
        f"/topics/{task_id or room_id}/compute-profile",
        json={"profile": "device", "device_id": device_id},
        headers=headers or session_auth_headers("alice"),
    )


def _profile(client, room_id, *, task_id=None, handle="alice") -> dict:
    r = client.get(
        f"/topics/{task_id or room_id}/compute-profile",
        headers=session_auth_headers(handle),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_a_task_keeps_its_own_computer_when_the_room_changes(client):
    project_id, room_id = _room(client)
    first, second, third = (
        _computer(client, project_id, name, owner="alice", team=True)
        for name in ("一号", "二号", "三号")
    )
    assert _pick(client, room_id, first).status_code == 200
    own = open_task(client, room_id, "有自己的电脑")

    picked = _pick(client, room_id, second, task_id=own["id"])
    assert picked.status_code == 200, picked.text
    assert _pick(client, room_id, third).status_code == 200

    mine = _profile(client, room_id, task_id=own["id"])
    assert mine["choice"]["device_id"] == second
    assert mine["follows_room"] is False
    assert _profile(client, room_id)["choice"]["device_id"] == third


def test_only_the_owner_changes_a_tasks_work_computer(client):
    project_id, room_id = _room(client)
    shared = _computer(client, project_id, "共用", owner="alice", team=True)
    task = open_task(client, room_id)

    refused = _pick(
        client, room_id, shared, task_id=task["id"], headers=session_auth_headers("bob")
    )

    assert refused.status_code == 403
    assert _profile(client, room_id, task_id=task["id"])["follows_room"] is True


def test_a_persons_own_computer_works_only_their_tasks(client):
    project_id, room_id = _room(client)
    laptop = _computer(client, project_id, "alice 的笔记本", owner="alice", team=False)
    shared = _computer(client, project_id, "共用", owner="alice", team=True)
    bobs = open_task(client, room_id, "bob 的任务", owner="bob", reviewer="alice")
    alices = open_task(client, room_id, "alice 的任务")
    bob = session_auth_headers("bob")

    offered = {
        d["device_id"]
        for d in _profile(client, room_id, task_id=bobs["id"], handle="bob")["devices"]
    }
    assert offered == {shared}
    assert (
        _pick(client, room_id, laptop, task_id=bobs["id"], headers=bob).status_code
        == 422
    )
    assert (
        _pick(client, room_id, shared, task_id=bobs["id"], headers=bob).status_code
        == 200
    )
    assert _pick(client, room_id, laptop, task_id=alices["id"]).status_code == 200


def test_a_task_does_not_take_someone_elses_computer_from_its_room(client):
    """A task fixes its computer the first time it needs one, from its room's.
    When the room works on a member's own computer and the task is someone
    else's, the task gets the project's default instead."""
    from app.domain.agent.compute_configs import fix_task_choice
    from app.domain.room_task.models import Task
    from app.domain.topic.models import Topic

    project_id, room_id = _room(client)
    laptop = _computer(client, project_id, "alice 的笔记本", owner="alice", team=False)
    assert _pick(client, room_id, laptop).status_code == 200
    bobs = open_task(client, room_id, "bob 的任务", owner="bob", reviewer="alice")
    alices = open_task(client, room_id, "alice 的任务")

    async def _fix(task_id: str) -> dict:
        async with client.test_request_factory() as session:
            topic = await session.get(Topic, uuid.UUID(room_id))
            task = await session.get(Task, uuid.UUID(task_id))
            project = await session.get(Project, uuid.UUID(project_id))
            await fix_task_choice(session, topic, task, project)
            return dict(task.compute_config or {})

    assert client.portal.call(_fix, bobs["id"]).get("device_id") != laptop
    assert client.portal.call(_fix, alices["id"])["device_id"] == laptop


def test_a_task_session_changes_its_own_tasks_computer_and_not_the_rooms(client):
    project_id, room_id = _room(client)
    first, second = (
        _computer(client, project_id, name, owner="alice", team=True)
        for name in ("一号", "二号")
    )
    assert _pick(client, room_id, first).status_code == 200
    task = open_task(client, room_id)
    credential = {
        "X-Cheese-Token": mint_scoped_token(
            project_id=project_id,
            topic_id=task["id"],
            agent_handle=room_agent_seat(client, room_id),
        )
    }

    moved = _pick(client, room_id, second, task_id=task["id"], headers=credential)

    assert moved.status_code == 200, moved.text
    assert _pick(client, room_id, second, headers=credential).status_code == 403
    assert (
        _profile(client, room_id, task_id=task["id"])["choice"]["device_id"] == second
    )
    assert _profile(client, room_id)["choice"]["device_id"] == first


def test_changing_the_room_moves_none_of_a_tasks_sessions(client, monkeypatch):
    """The room's sessions push and move; a task with its own computer is not
    pushed from it or taken off it."""
    from app.domain.agent import execution
    from app.domain.agent.compute_configs import ComputeChoice
    from app.domain.agent.device_hub import device_hub
    from app.domain.agent_session.services import AgentSessionService
    from tests.executor_release import running

    monkeypatch.setattr(device_hub, "is_online", lambda _device_id: True)
    pushes: list[str] = []

    async def call(target, method, params, **_kwargs):
        if method == "ping":
            return running()
        if method == "control":
            pushes.append(target["device_id"])
        return {"value": {"stdout": "", "stderr": "", "interrupted": False}}

    monkeypatch.setattr(execution, "call", call)
    project_id, room_id = _room(client)
    here, there = (
        _computer(client, project_id, name, owner="alice", team=True)
        for name in ("这里", "那里")
    )
    assert _pick(client, room_id, here).status_code == 200
    task = open_task(client, room_id)
    assert _pick(client, room_id, here, task_id=task["id"]).status_code == 200
    choice = ComputeChoice(profile="device", device_id=here)
    lease = {
        "kind": "device",
        "device_id": here,
        "status": "ready",
        "home": "/here",
        "state": "/here/state",
    }

    async def _working(conversation_id: str, handle: str) -> str:
        async with client.test_request_factory() as session:
            row = await AgentSessionService(session).ensure(
                uuid.UUID(conversation_id), handle, harness="pi"
            )
            row.execution_request = {
                "generation": str(uuid.uuid4()),
                "choice": choice.model_dump(),
                "authorized_by": None,
            }
            row.work_lease = lease
            await session.commit()
            return str(row.id)

    in_room = client.portal.call(_working, room_id, "analyst")
    in_task = client.portal.call(_working, task["id"], "analyst")

    moved = _pick(client, room_id, there)

    assert moved.status_code == 200, moved.text
    assert pushes == [here]
    room_sessions = {s["id"]: s for s in _profile(client, room_id)["sessions"]}
    task_sessions = {
        s["id"]: s for s in _profile(client, room_id, task_id=task["id"])["sessions"]
    }
    assert room_sessions[in_room]["choice"]["device_id"] == there
    assert task_sessions[in_task]["choice"]["device_id"] == here
    assert task_sessions[in_task]["lease"] is not None


def _holding(client, conversation_id: str, device_id: str) -> str:
    """A session of this conversation working on ``device_id`` now."""
    from app.domain.agent.compute_configs import ComputeChoice
    from app.domain.agent_session.services import AgentSessionService

    async def _write() -> str:
        async with client.test_request_factory() as session:
            row = await AgentSessionService(session).ensure(
                uuid.UUID(conversation_id), "analyst", harness="pi"
            )
            row.execution_request = {
                "generation": str(uuid.uuid4()),
                "choice": ComputeChoice(
                    profile="device", device_id=device_id
                ).model_dump(),
                "authorized_by": None,
            }
            row.work_lease = {
                "kind": "device",
                "device_id": device_id,
                "status": "ready",
                "home": "/home",
                "state": "/home/state",
            }
            await session.commit()
            return str(row.id)

    return client.portal.call(_write)


def _machines_answer(monkeypatch) -> list[str]:
    """Every machine answers, and a push is recorded by the device it ran on."""
    from app.domain.agent import execution
    from app.domain.agent.device_hub import device_hub
    from tests.executor_release import running

    monkeypatch.setattr(device_hub, "is_online", lambda _device_id: True)
    pushes: list[str] = []

    async def call(target, method, params, **_kwargs):
        if method == "ping":
            return running()
        if method == "control":
            pushes.append(target["device_id"])
        return {"value": {"stdout": "", "stderr": "", "interrupted": False}}

    monkeypatch.setattr(execution, "call", call)
    return pushes


def test_a_manager_or_the_devices_owner_moves_a_task_off_a_device(client, monkeypatch):
    """Not only the task's owner: a project manager, and whoever owns the
    device the task is on, may take it off. Another member may not."""
    _machines_answer(monkeypatch)
    project_id, room_id = _room(client)
    join_project_team(client, project_id, "dave")
    join_project_team(client, project_id, "eve")
    daves = _computer(client, project_id, "dave 的服务器", owner="dave", team=True)
    elsewhere = _computer(client, project_id, "另一台", owner="alice", team=True)
    task = open_task(client, room_id, "bob 的任务", owner="bob", reviewer="alice")
    _holding(client, task["id"], daves)

    def move(handle: str):
        return _pick(
            client,
            room_id,
            elsewhere,
            task_id=task["id"],
            headers=session_auth_headers(handle),
        )

    assert move("eve").status_code == 403
    assert move("dave").status_code == 200, move("dave").text
    assert move("alice").status_code == 200


def test_handing_over_takes_a_task_off_its_former_owners_computer(client, monkeypatch):
    pushes = _machines_answer(monkeypatch)
    project_id, room_id = _room(client)
    client.post(
        f"/topics/{room_id}/members",
        json={"handle": "bob", "role": "member", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    laptop = _computer(client, project_id, "alice 的笔记本", owner="alice", team=False)
    shared = _computer(client, project_id, "共用", owner="alice", team=True)
    assert _pick(client, room_id, shared).status_code == 200
    task = open_task(client, room_id)
    assert _pick(client, room_id, laptop, task_id=task["id"]).status_code == 200
    working = _holding(client, task["id"], laptop)

    handed = client.patch(
        f"/topics/{task['id']}/task",
        json={"owner_handle": "bob"},
        headers=session_auth_headers("alice"),
    )

    assert handed.status_code == 200, handed.text
    assert handed.json()["data"]["owner_handle"] == "bob"
    assert pushes == [laptop]
    profile = _profile(client, room_id, task_id=task["id"], handle="bob")
    assert profile["choice"]["device_id"] == shared
    session = next(s for s in profile["sessions"] if s["id"] == working)
    assert session["lease"] is None
