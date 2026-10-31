"""A member's own Claude Code: its session on its owner's machine, its tools in
its execution environment (#2991, #2320).

Its session runs on the computer holding the model connection its owner added
it with, with the login the owner gave the platform there: not on another of
its owner's computers, and never on another member's. What
it runs — the files, the commands — goes to the execution environment its
conversation has, like any agent's, which is its owner's computer only when
that is the environment.
"""

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.sandbox_auth import bind_resource_token, mint_scoped_token
from app.domain.agent import execution
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.channel import ScreenSetupError, mint_session_token
from app.domain.agent.owner_provider import OWNER_CHANNEL, OwnerChannel
from app.domain.agent.place import OWN_LOGIN_ID
from app.domain.agent.turn_speakers import private_memory_for
from app.domain.agent_instance.models import AgentInstance, OwnAgent
from app.domain.agent_session.services import AgentSessionService
from app.domain.device.login_kind import FIRST_LOGIN
from app.domain.device.models import DeviceClaudeLoginRow
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.identity.services import IdentityService
from app.domain.machine import session_work as work_lease
from app.domain.project.models import Project
from app.domain.topic.models import Topic
from app.domain.user.models import User
from tests.integration.conftest import post_project, session_auth_headers
from tests.support.devices import enrollment_team, place_in_project_team

pytestmark = pytest.mark.anyio


async def _user(db, handle):
    user = await db.scalar(select(User).where(User.username == handle))
    if user is None:
        user = User(
            username=handle,
            email=f"{handle}@example.test",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.add(user)
        await db.flush()
    return user


async def _machine(devices, name, owner, project_id=None):
    code = await devices.start(name)
    device = await devices.approve(
        code,
        owner_user_id=owner.id,
        supply=Supply.self_hosted,
        team_id=await enrollment_team(devices, owner.id),
    )
    if project_id is not None:
        await place_in_project_team(
            devices, device.device_id, project_id, actor_user_id=owner.id
        )
    return device.device_id


def _hub(online=lambda device: True):
    async def install(device, argv, **kwargs):
        return {
            "exit": 0,
            "stdout": json.dumps(
                {
                    "state": f"/{device}/state",
                    "workspace": f"/{device}/work",
                    "mcp_servers": [],
                }
            ),
        }

    return SimpleNamespace(
        target=lambda _device: "linux-amd64",
        is_online=online,
        reconnecting=lambda device: False,
        exec=AsyncMock(side_effect=install),
        all_online_screens=lambda: [],
    )


def _executor(target, method, *args, **_kwargs):
    if method == "ping":
        import hashlib

        from app.domain.agent.harness.claude_code.remote_execution import (
            launch,
            runtime,
        )

        return {
            "capabilities": ["prepare"],
            "sandbox": True,
            "protocol_version": runtime.PROTOCOL_VERSION,
            "files": {
                name: hashlib.sha256(value.encode()).hexdigest()
                for name, value in launch.file_sources().items()
            },
        }
    if method == "prepare":
        return target
    if method == "control":
        return {"tasks": []}
    return {"device": target["device_id"]}


async def _own_session(client, *, environment_on):
    """A project whose room works on Alice's computer, and Bob's own Claude
    Code in it, its session on Bob's laptop. ``environment_on`` names whose
    computer the conversation's execution environment is: the room's (Alice's)
    or Bob's laptop, attached to the project. Alice gave the platform a Claude
    Code login on her computer too."""
    project = post_project(client, json={"name": "Own hands"}, owner="alice").json()[
        "data"
    ]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    project_id, topic_id = uuid.UUID(project["id"]), uuid.UUID(room["id"])
    async with client.test_factory() as db:
        devices = sql_device_service(db)
        alice, bob = await _user(db, "alice"), await _user(db, "bob")
        alices_desk = await _machine(devices, "alices-desk", alice, project_id)
        bobs_laptop = await _machine(devices, "bobs-laptop", bob, project_id)
        instance = AgentInstance(
            project_id=project_id,
            handle=f"own-{uuid.uuid4().hex[:8]}",
            configuration={},
            display_name="bob的 Claude Code",
        )
        db.add(instance)
        await db.flush()
        for machine in (bobs_laptop, alices_desk):
            db.add(
                DeviceClaudeLoginRow(
                    team_id=(await db.get(Project, project_id)).team_id,
                    device_id=machine,
                    installed=True,
                    logged_in=True,
                    auth_method="claude.ai",
                    checked_at=datetime.now(UTC),
                    allowed_models=["sonnet", "opus"],
                )
            )
        await db.flush()
        db.add(
            OwnAgent(
                instance_id=instance.id,
                owner_user_id=bob.id,
                harness="claude-code",
                login_id=bobs_laptop,
            )
        )
        actor = (await IdentityService(db).ensure_room_agent_user(topic_id)).username
        topic = await db.get(Topic, topic_id)
        topic.default_environment = {
            "kind": "self_hosted",
            "device_id": {"alice": alices_desk, "bob": bobs_laptop}[environment_on],
        }
        resource = str(topic.resource_id or topic_id)
        # A session names its agent by the instance's handle, as a turn does.
        session = await AgentSessionService(db).ensure(
            topic_id, instance.handle, harness="claude-code"
        )
        session.runtime_location = {
            "device_id": bobs_laptop,
            "resource_id": resource,
            "channel": OWNER_CHANNEL,
        }
        token = bind_resource_token(
            mint_scoped_token(
                project_id=str(project_id),
                topic_id=str(topic_id),
                agent_handle=actor,
            ),
            resource,
            session_id=str(session.id),
        )
        await db.commit()
    return SimpleNamespace(
        project_id=project_id,
        topic_id=topic_id,
        session_id=session.id,
        token=token,
        bobs_laptop=bobs_laptop,
        alices_desk=alices_desk,
        handle=instance.handle,
    )


def _lease(client, own):
    return client.post(
        f"/topics/{own.topic_id}/sessions/{own.session_id}/work-lease",
        headers={"Authorization": f"Bearer {own.token}"},
        json={"env": {}},
    )


async def test_an_own_agents_tools_run_in_its_environment_not_its_owners_machine(
    client, monkeypatch
):
    own = await _own_session(client, environment_on="alice")
    monkeypatch.setattr(work_lease, "device_hub", _hub())
    monkeypatch.setattr(execution, "call", AsyncMock(side_effect=_executor))

    response = _lease(client, own)

    assert response.status_code == 200, response.text
    assert response.json()["data"]["target"]["device_id"] == own.alices_desk


async def test_an_own_agent_whose_environment_is_its_owners_machine_works_there(
    client, monkeypatch
):
    own = await _own_session(client, environment_on="bob")
    monkeypatch.setattr(work_lease, "device_hub", _hub())
    monkeypatch.setattr(execution, "call", AsyncMock(side_effect=_executor))

    response = _lease(client, own)

    assert response.status_code == 200, response.text
    assert response.json()["data"]["target"]["device_id"] == own.bobs_laptop


async def test_private_memory_is_carried_only_in_its_owners_chat_and_on_their_machine(
    client,
):
    """A turn carries a person's private memory only in that person's private
    chat with the agent. Bob's own Claude Code runs its session on Bob's
    laptop, which Bob reads: in Bob's chat with it, Bob's; in Alice's chat
    with it, nobody's. The room's own agent, on the platform's session host,
    carries the person in whichever private chat it is in. No shared room
    carries anybody's."""
    own = await _own_session(client, environment_on="bob")

    async def given(chat_of: str | None) -> tuple[str | None, str | None]:
        async with client.test_request_factory() as db:
            instances = (
                await db.execute(
                    select(AgentInstance).where(
                        AgentInstance.project_id == own.project_id
                    )
                )
            ).scalars()
            mine = room = None
            for instance in instances:
                carried = await private_memory_for(
                    db, instance.id, private_owner=chat_of
                )
                if instance.handle == own.handle:
                    mine = carried
                else:
                    room = carried
            return mine, room

    assert client.portal.call(given, "bob") == ("bob", "bob")
    assert client.portal.call(given, "alice") == (None, "alice")
    assert client.portal.call(given, None) == (None, None)


async def test_an_own_agents_conversation_starts_on_its_owners_machine(client):
    own = await _own_session(client, environment_on="alice")
    channel = OwnerChannel(
        DeviceChannel(hub=_hub(), session_factory=client.test_request_factory)
    )

    placement = client.portal.call(
        lambda: channel.precheck(
            SessionRef(own.project_id, own.topic_id, own.handle, harness="claude-code"),
            needs_place=False,
        )
    )

    assert placement.machine == own.bobs_laptop


async def test_another_members_machine_is_never_an_own_agents_session_host(client):
    # Alice's computer is online, logged in to Claude Code, and the very one
    # the conversation's tools run on; Bob's laptop is away.
    own = await _own_session(client, environment_on="alice")
    channel = OwnerChannel(
        DeviceChannel(
            hub=_hub(online=lambda device: device == own.alices_desk),
            session_factory=client.test_request_factory,
        )
    )

    with pytest.raises(ScreenSetupError):
        client.portal.call(
            lambda: channel.precheck(
                SessionRef(
                    own.project_id, own.topic_id, own.handle, harness="claude-code"
                ),
                needs_place=False,
            )
        )


async def _bobs_desk(client, own) -> str:
    """Bob's second computer, online with another connection of his: a custom
    model service, which the project allows like any other."""
    async with client.test_factory() as db:
        desk = await _machine(
            sql_device_service(db), "bobs-desk", await _user(db, "bob"), own.project_id
        )
        db.add(
            DeviceClaudeLoginRow(
                device_id=desk,
                team_id=(await db.get(Project, own.project_id)).team_id,
                installed=True,
                logged_in=True,
                auth_method="claude.ai",
                subscription_type="pro",
                checked_at=datetime.now(UTC),
                allowed_models=["sonnet", "opus"],
            )
        )
        await db.commit()
    return desk


def _precheck(client, own, online):
    channel = OwnerChannel(
        DeviceChannel(
            hub=_hub(online=online), session_factory=client.test_request_factory
        )
    )
    return client.portal.call(
        lambda: channel.precheck(
            SessionRef(own.project_id, own.topic_id, own.handle, harness="claude-code"),
            needs_place=False,
        )
    )


async def test_an_own_agents_session_runs_only_where_its_connection_is(client):
    # Bob added his agent with his laptop's login. The laptop is away; his desk
    # is online with another of his connections.
    own = await _own_session(client, environment_on="alice")
    desk = await _bobs_desk(client, own)

    with pytest.raises(ScreenSetupError):
        _precheck(client, own, online=lambda device: device == desk)

    placement = _precheck(client, own, online=lambda device: True)
    assert placement.machine == own.bobs_laptop


async def test_an_own_agent_whose_connection_was_removed_starts_nothing(client):
    own = await _own_session(client, environment_on="alice")
    desk = await _bobs_desk(client, own)
    async with client.test_factory() as db:
        login = await db.get(DeviceClaudeLoginRow, own.bobs_laptop)
        login.revoked_at, login.logged_in = datetime.now(UTC), False
        await db.commit()

    with pytest.raises(ScreenSetupError) as refused:
        _precheck(client, own, online=lambda device: device in (own.bobs_laptop, desk))
    assert str(refused.value), "the room receives a reason for refusing to start"


async def _launch_env(client, own, *, leased_on):
    """The environment Bob's laptop is handed to start his own Claude Code
    with, once its lease is ready on ``leased_on`` and that machine answers."""
    async with client.test_factory() as db:
        row = await AgentSessionService(db).ensure(
            own.topic_id, own.handle, harness="claude-code"
        )
        row.work_lease = {
            "kind": "device",
            "device_id": leased_on,
            "generation": str(uuid.uuid4()),
            "status": "ready",
            "workspace": "/where/the/executor/holds/it",
            "url": "http://platform/execution",
            "state": "/where/the/executor/keeps/state",
        }
        await db.commit()
    channel = OwnerChannel(
        DeviceChannel(hub=_hub(), session_factory=client.test_request_factory)
    )
    session = SessionRef(
        own.project_id, own.topic_id, own.handle, harness="claude-code"
    )

    async def prepare():
        precheck = await channel.precheck(session, needs_place=True)
        token = mint_session_token(own.project_id, own.topic_id, precheck.agent_handle)
        async with channel.prepare_session(
            session=session, token=token, env={}, precheck=precheck
        ) as prepared:
            return precheck.machine, dict(prepared.env)

    return client.portal.call(prepare)


async def _launch_target(client, own, *, leased_on):
    """The execution target Bob's laptop is handed with that environment."""
    _machine, env = await _launch_env(client, own, leased_on=leased_on)
    return json.loads(env["CHEESE_EXECUTION_TARGET"])


async def _on_a_second_login(client, own) -> str:
    """Bob's laptop holds a second login too, and his agent was added with
    that one; the first stays logged in. The second's record id."""
    async with client.test_factory() as db:
        second = DeviceClaudeLoginRow(
            team_id=(await db.get(Project, own.project_id)).team_id,
            device_id=own.bobs_laptop,
            local_id="B7QX",
            installed=True,
            logged_in=True,
            auth_method="claude.ai",
            subscription_type="pro",
            allowed_models=["sonnet", "opus"],
            checked_at=datetime.now(UTC),
        )
        db.add(second)
        await db.flush()
        agent = await db.scalar(
            select(OwnAgent)
            .join(AgentInstance, AgentInstance.id == OwnAgent.instance_id)
            .where(AgentInstance.handle == own.handle)
        )
        agent.login_id = second.id
        await db.commit()
        return second.id


async def test_an_own_session_signs_in_with_the_login_it_was_added_with(
    client, monkeypatch
):
    own = await _own_session(client, environment_on="bob")
    monkeypatch.setattr(execution, "call", AsyncMock(return_value={}))

    machine, env = await _launch_env(client, own, leased_on=own.bobs_laptop)
    assert (machine, env[OWN_LOGIN_ID]) == (own.bobs_laptop, FIRST_LOGIN)

    await _on_a_second_login(client, own)
    machine, env = await _launch_env(client, own, leased_on=own.bobs_laptop)
    assert (machine, env[OWN_LOGIN_ID]) == (own.bobs_laptop, "B7QX")


async def test_an_own_agent_on_a_removed_login_starts_nothing_beside_one_in_use(
    client,
):
    """Its machine's first login is still logged in, but it is not the one
    the agent was added with."""
    own = await _own_session(client, environment_on="alice")
    second = await _on_a_second_login(client, own)
    async with client.test_factory() as db:
        login = await db.get(DeviceClaudeLoginRow, second)
        login.revoked_at, login.logged_in = datetime.now(UTC), False
        await db.commit()

    with pytest.raises(ScreenSetupError) as refused:
        _precheck(client, own, online=lambda device: True)
    assert str(refused.value), "the room receives a reason for refusing to start"


async def test_an_own_session_is_told_when_its_executor_is_on_another_machine(
    client, monkeypatch
):
    # Bob's laptop has no view of a project held elsewhere: it must start the
    # session in a directory of its own, not at a path it does not have.
    own = await _own_session(client, environment_on="alice")
    monkeypatch.setattr(execution, "call", AsyncMock(return_value={}))

    target = await _launch_target(client, own, leased_on=own.alices_desk)

    assert target["workspace"] == "/where/the/executor/holds/it"
    assert target.get("local") and target.get("apart")


async def test_an_own_session_on_its_executors_machine_sees_the_project_there(
    client, monkeypatch
):
    own = await _own_session(client, environment_on="bob")
    monkeypatch.setattr(execution, "call", AsyncMock(return_value={}))

    target = await _launch_target(client, own, leased_on=own.bobs_laptop)

    assert target["workspace"] == "/where/the/executor/holds/it"
    assert target.get("local") and not target.get("apart")
