"""What a room sees of an enrolled machine: an isolated environment, or the
whole machine (#2320 step 2).

Isolated is what a room gets when nobody chose; the whole machine is its
owner's to give, signed in as a person. A machine whose system has no
isolated environment yet says so, and a room there that runs isolated is told
what to do instead of being run over the whole machine.
"""

import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.sandbox_auth import bind_resource_token, mint_scoped_token
from app.domain.agent.device_hub import device_hub
from app.domain.agent_session.services import AgentSessionService
from app.domain.device.supply import Supply, Visibility
from app.domain.device.wiring import sql_device_service
from app.domain.identity.services import IdentityService
from app.domain.machine import session_work as work_lease
from app.domain.topic.models import Topic
from app.domain.user.models import User
from tests.integration.conftest import (
    join_project_team,
    post_project,
    room_agent_headers,
    session_auth_headers,
)

OWNER, MEMBER = "machine-owner", "teammate"


def _room(client) -> tuple[str, str]:
    """A project the machine's owner runs, a teammate on its team, and a room."""
    pid = post_project(client, json={"name": "P"}, owner=OWNER).json()["data"]["id"]
    join_project_team(client, pid, MEMBER)
    tid = client.post(
        "/topics",
        json={"project_id": pid, "title": "T"},
        headers=session_auth_headers(OWNER),
    ).json()["data"]["id"]
    return pid, tid


def _machine(client, pid: str, name: str = "workstation", *, owner: str = OWNER):
    """A machine ``owner`` enrolled and lets the project use."""

    async def _enroll() -> str:
        async with client.test_factory() as session:
            user = await session.scalar(select(User).where(User.username == owner))
            service = sql_device_service(session)
            device = await service.approve(
                await service.start(name),
                owner_user_id=user.id,
                supply=Supply.self_hosted,
            )
            await service.assign_to_project(
                device.device_id, uuid.UUID(pid), actor_user_id=user.id
            )
            await session.commit()
            return device.device_id

    return asyncio.run(_enroll())


def _binding(client, tid: str):
    async def _read():
        async with client.test_factory() as session:
            return await sql_device_service(session).topic_binding(uuid.UUID(tid))

    return asyncio.run(_read())


def _choose(client, tid: str, device_id: str, headers, visibility=None):
    body = {"profile": "device", "device_id": device_id}
    if visibility is not None:
        body["visibility"] = visibility
    return client.put(f"/topics/{tid}/compute-profile", json=body, headers=headers)


def _profile(client, tid: str, headers) -> dict:
    response = client.get(f"/topics/{tid}/compute-profile", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_a_room_given_a_machine_without_a_choice_runs_isolated(client):
    pid, tid = _room(client)
    machine = _machine(client, pid)

    chosen = _choose(client, tid, machine, session_auth_headers(MEMBER))

    assert chosen.status_code == 200, chosen.text
    assert _binding(client, tid).visibility is Visibility.isolated
    visibility = _profile(client, tid, session_auth_headers(MEMBER))["visibility"]
    assert visibility["effective"] == "isolated"
    assert visibility["machine_access"] is False
    options = {option["id"]: option for option in visibility["options"]}
    assert options["isolated"]["default"] is True
    assert options["isolated"]["available"] is True
    assert options["host"]["default"] is False


def test_the_machines_owner_can_give_a_room_the_whole_machine(client):
    pid, tid = _room(client)
    machine = _machine(client, pid)
    assert (
        _choose(client, tid, machine, session_auth_headers(MEMBER)).status_code == 200
    )

    given = _choose(client, tid, machine, session_auth_headers(OWNER), "host")

    assert given.status_code == 200, given.text
    assert _binding(client, tid).visibility is Visibility.host
    visibility = _profile(client, tid, session_auth_headers(OWNER))["visibility"]
    assert visibility["effective"] == "host"
    assert visibility["machine_access"] is True


def test_nobody_but_the_machines_owner_gives_a_room_the_whole_machine(client):
    pid, tid = _room(client)
    machine = _machine(client, pid)

    refused = _choose(client, tid, machine, session_auth_headers(MEMBER), "host")

    assert refused.status_code == 403
    owner_only = "只有电脑的主人本人登录后，才能让频道访问整台电脑"
    assert owner_only in refused.json()["message"]
    assert _binding(client, tid) is None


def test_a_room_cannot_let_itself_out_with_its_own_credential(client):
    """The agent of a room asks with the room's credential, which is never the
    machine's owner however it is resolved: a room running isolated cannot
    give itself the whole machine."""
    pid, tid = _room(client)
    machine = _machine(client, pid)
    assert _choose(client, tid, machine, session_auth_headers(OWNER)).status_code == 200

    refused = _choose(client, tid, machine, room_agent_headers(client, tid), "host")

    assert refused.status_code == 403, refused.text
    assert _binding(client, tid).visibility is Visibility.isolated


def test_anyone_in_the_room_can_take_the_whole_machine_back(client):
    pid, tid = _room(client)
    machine = _machine(client, pid)
    assert (
        _choose(client, tid, machine, session_auth_headers(OWNER), "host").status_code
        == 200
    )

    narrowed = _choose(client, tid, machine, session_auth_headers(MEMBER), "isolated")

    assert narrowed.status_code == 200, narrowed.text
    assert _binding(client, tid).visibility is Visibility.isolated


def test_a_room_keeps_its_access_when_it_is_chosen_again_without_one(client):
    """A room that already has the whole machine keeps it when someone picks
    the same machine again and says nothing about access; this is how every
    room enrolled before the choice existed goes on."""
    pid, tid = _room(client)
    machine = _machine(client, pid)
    assert (
        _choose(client, tid, machine, session_auth_headers(OWNER), "host").status_code
        == 200
    )

    again = _choose(client, tid, machine, session_auth_headers(MEMBER))

    assert again.status_code == 200, again.text
    assert _binding(client, tid).visibility is Visibility.host


def test_a_room_moved_to_another_machine_starts_isolated_there(client):
    pid, tid = _room(client)
    first = _machine(client, pid, "first")
    second = _machine(client, pid, "second")
    assert (
        _choose(client, tid, first, session_auth_headers(OWNER), "host").status_code
        == 200
    )

    moved = _choose(client, tid, second, session_auth_headers(OWNER))

    assert moved.status_code == 200, moved.text
    binding = _binding(client, tid)
    assert (binding.device_id, binding.visibility) == (second, Visibility.isolated)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"profile": "device", "visibility": "host"}, "visibilityNeedsDevice"),
        ({"profile": "device", "visibility": "everything"}, "visibilityInvalid"),
    ],
)
def test_access_is_chosen_for_a_named_machine_and_only_as_one_of_two(
    client, monkeypatch, body, message
):
    from app.core.sentences import say

    async def online(*_args, **_kwargs) -> bool:
        return True

    monkeypatch.setattr("app.api.routes.topics_compute.project_device_online", online)
    pid, tid = _room(client)
    machine = _machine(client, pid)
    if body["visibility"] == "everything":
        body = {**body, "device_id": machine}

    response = client.put(
        f"/topics/{tid}/compute-profile", json=body, headers=session_auth_headers(OWNER)
    )

    assert response.status_code == 422, response.text
    assert say(message) in response.json()["message"]


def test_the_picker_says_whose_machine_it_is_and_which_cannot_isolate(
    client, monkeypatch
):
    pid, tid = _room(client)
    linux = _machine(client, pid, "linux box")
    mac = _machine(client, pid, "laptop")
    windows = _machine(client, pid, "desktop")
    targets = {linux: "linux-amd64", mac: "darwin-arm64", windows: "windows-amd64"}
    monkeypatch.setattr(device_hub, "target", lambda device_id: targets[device_id])

    def listed(headers) -> dict:
        devices = _profile(client, tid, headers)["devices"]
        return {device["device_id"]: device for device in devices}

    as_owner, as_member = (
        listed(session_auth_headers(OWNER)),
        listed(session_auth_headers(MEMBER)),
    )

    assert {device["owned"] for device in as_owner.values()} == {True}
    assert {device["owned"] for device in as_member.values()} == {False}
    assert as_owner[linux]["sandbox_unavailable"] is None
    assert as_owner[mac]["sandbox_unavailable"] is None
    assert as_owner[windows]["sandbox_unavailable"]["key"] == (
        "sandboxUnavailableWindows"
    )


async def _session_on(client, tid: str, device_id: str) -> tuple[str, str]:
    """An agent session of the room about to take hands, and its credential."""
    async with client.test_factory() as db:
        topic = await db.get(Topic, uuid.UUID(tid))
        resource = str(topic.resource_id or topic.id)
        agent = await IdentityService(db).ensure_room_agent_user(topic.id)
        session = await AgentSessionService(db).ensure(
            topic.id, "analyst", harness="claude-code"
        )
        session.runtime_location = {
            "device_id": "center",
            "resource_id": resource,
            "channel": "device",
        }
        topic.compute_config = {
            "name": "laptop",
            "profile": "device",
            "device_id": device_id,
        }
        await db.commit()
        token = bind_resource_token(
            mint_scoped_token(
                project_id=str(topic.project_id),
                topic_id=tid,
                agent_handle=agent.username,
            ),
            resource,
            session_id=str(session.id),
        )
        return str(session.id), token


def _hub(target: str, install=None):
    return SimpleNamespace(
        is_online=lambda _device: True,
        reconnecting=lambda _device: False,
        target=lambda _device: target,
        exec=AsyncMock(
            side_effect=install
            or AssertionError("nothing is installed on this machine")
        ),
    )


def test_an_isolated_room_on_windows_is_told_what_to_do_instead(client, monkeypatch):
    """Windows has no isolated environment of its own: the room's agent is
    told to use WSL or ask for full access, nothing is installed on the
    machine, and the conversation goes on."""
    from app.core.sentences import say

    pid, tid = _room(client)
    pc = _machine(client, pid, "desktop")
    assert _choose(client, tid, pc, session_auth_headers(OWNER)).status_code == 200
    hub = _hub("windows-amd64")
    monkeypatch.setattr(work_lease, "device_hub", hub)
    session_id, token = asyncio.run(_session_on(client, tid, pc))

    answer = client.post(
        f"/topics/{tid}/sessions/{session_id}/work-lease",
        headers={"X-Cheese-Token": token},
        json={"env": {}},
    )

    assert answer.status_code == 200, answer.text
    assert answer.json()["data"] == {
        "unavailable": str(say("sandboxUnavailableWindows"))
    }
    hub.exec.assert_not_awaited()


@pytest.mark.parametrize(("visibility", "sandbox"), [(None, True), ("host", False)])
def test_the_install_is_asked_for_the_rooms_access_to_the_machine(
    client, monkeypatch, visibility, sandbox
):
    """The executor is installed isolated, or over the whole machine when its
    owner gave the room that — and never told it may install anything there,
    since the machine is a person's."""
    from app.domain.agent.harness.claude_code import executor_launch

    pid, tid = _room(client)
    machine = _machine(client, pid)
    assert (
        _choose(
            client, tid, machine, session_auth_headers(OWNER), visibility
        ).status_code
        == 200
    )
    asked = {}

    def capture(project, resource, env, **options):
        asked.update(options)
        return "print('installed')"

    monkeypatch.setattr(executor_launch, "script", capture)

    async def install(_device, _argv, **_kwargs):
        return {
            "exit": 0,
            "stdout": json.dumps(
                {"state": "/state", "workspace": "/work", "mcp_servers": []}
            ),
        }

    monkeypatch.setattr(work_lease, "device_hub", _hub("linux-amd64", install))
    monkeypatch.setattr(
        work_lease, "environment_status", AsyncMock(return_value={"state": "ready"})
    )
    from app.domain.agent import execution

    monkeypatch.setattr(execution, "call", AsyncMock(return_value={}))
    session_id, token = asyncio.run(_session_on(client, tid, machine))

    answer = client.post(
        f"/topics/{tid}/sessions/{session_id}/work-lease",
        headers={"X-Cheese-Token": token},
        json={"env": {}},
    )

    assert answer.status_code == 200, answer.text
    assert asked == {"sandbox": sandbox, "platform_machine": False}


def test_an_install_that_fails_on_the_machine_is_told_not_a_server_error(
    client, monkeypatch
):
    """The install exits non-zero on the machine (here: its Python cannot
    open https). The room's agent gets the machine's last line of why, not a
    500 with nothing in it."""
    pid, tid = _room(client)
    machine = _machine(client, pid)
    assert _choose(client, tid, machine, session_auth_headers(OWNER)).status_code == 200
    why = "urllib.error.URLError: <urlopen error unknown url type: https>"

    async def fail(_device, _argv, **_kwargs):
        return {
            "exit": 1,
            "stderr": "Traceback (most recent call last):\n  ...\n" + why + "\n",
        }

    monkeypatch.setattr(work_lease, "device_hub", _hub("linux-amd64", fail))
    session_id, token = asyncio.run(_session_on(client, tid, machine))

    answer = client.post(
        f"/topics/{tid}/sessions/{session_id}/work-lease",
        headers={"X-Cheese-Token": token},
        json={"env": {}},
    )

    assert answer.status_code == 200, answer.text
    assert why in answer.json()["data"]["unavailable"]


def test_a_machine_that_cannot_isolate_the_room_is_heard_out(client, monkeypatch):
    """A Linux machine without bubblewrap refuses the install with what to
    install; the room's agent gets those words, not a failed request, told
    whose machine it is and that the room can go on elsewhere."""
    pid, tid = _room(client)
    machine = _machine(client, pid)
    assert _choose(client, tid, machine, session_auth_headers(OWNER)).status_code == 200
    from app.domain.agent.harness.claude_code.remote_execution import bootstrap

    said = "This room runs in an isolated environment, which needs bubblewrap."

    async def refuse(_device, _argv, **_kwargs):
        return {"exit": bootstrap.SANDBOX_UNAVAILABLE_EXIT, "stderr": said + "\n"}

    monkeypatch.setattr(work_lease, "device_hub", _hub("linux-amd64", refuse))
    session_id, token = asyncio.run(_session_on(client, tid, machine))

    answer = client.post(
        f"/topics/{tid}/sessions/{session_id}/work-lease",
        headers={"X-Cheese-Token": token},
        json={"env": {}},
    )

    assert answer.status_code == 200, answer.text
    told = answer.json()["data"]["unavailable"]
    assert said in told
    assert "workstation" in told
    assert OWNER in told
    assert told != said
