"""First execution acquires the room's hands for the session asking.

一个话题一个容器（2026-09-28，推翻结论 60）：房间里的每一条会话都落在房间那一项算
出来的那台机器上；换机器是整个房间一起换。
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.sandbox_auth import bind_resource_token, mint_scoped_token
from app.domain.agent import execution
from app.domain.agent.device_hub import DeviceOffline
from app.domain.agent.dispatch_log import DispatchRow
from app.domain.agent_session.services import AgentSessionService
from app.domain.device.supply import Supply, Visibility
from app.domain.device.wiring import sql_device_service
from app.domain.identity.services import IdentityService
from app.domain.machine import session_work as work_lease
from app.domain.topic.models import Topic
from app.domain.user.models import User
from tests.executor_release import running
from tests.integration.conftest import post_project, session_auth_headers
from tests.support.hang import HANG_S

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("background_state", ["running", "unresponsive"])
@pytest.mark.parametrize("old_online", [True, False])
async def test_every_session_in_a_room_acquires_the_rooms_device(
    client, monkeypatch, old_online, background_state
):
    project = post_project(
        client, json={"name": "Session hands"}, owner="alice"
    ).json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    project_id, topic_id = uuid.UUID(project["id"]), uuid.UUID(room["id"])
    async with client.test_factory() as db:
        devices = sql_device_service(db)
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
        agent = await IdentityService(db).ensure_room_agent_user(topic_id)
        actor_handle = agent.username
        topic = await db.get(Topic, topic_id)
        resource = str(topic.resource_id or topic_id)
        identities = []
        for handle in ("ada", "bob"):
            code = await devices.start(handle)
            device = await devices.approve(
                code,
                owner_user_id=owner.id,
                supply=Supply.self_hosted,
                visibility=Visibility.host,
            )
            await devices.assign_to_project(
                device.device_id, project_id, actor_user_id=owner.id
            )
            session = await AgentSessionService(db).ensure(
                topic_id, handle, harness="claude-code"
            )
            session.execution_request = {
                "generation": str(uuid.uuid4()),
                "choice": {
                    "name": handle,
                    "profile": "device",
                    "device_id": device.device_id,
                },
            }
            session.runtime_location = {
                "device_id": "center",
                "resource_id": resource,
                "channel": "device",
            }
            token = bind_resource_token(
                mint_scoped_token(
                    project_id=str(project_id),
                    topic_id=str(topic_id),
                    agent_handle=actor_handle,
                ),
                resource,
                session_id=str(session.id),
            )
            identities.append((session.id, device.device_id, token))
            assert session.work_lease is None
        # 房间的选择是第一台：两条会话各自那份 ``choice`` 指着不同的机器，也都落在这
        # 一台上——会话行上那一份不是来源。
        topic.compute_config = {
            "name": "ada",
            "profile": "device",
            "device_id": identities[0][1],
        }
        await db.commit()

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

    hub = SimpleNamespace(
        is_online=lambda device: True,
        reconnecting=lambda device: False,
        exec=AsyncMock(side_effect=install),
    )
    monkeypatch.setattr(work_lease, "device_hub", hub)

    def executor_answer(target, method, *args, **_kwargs):
        if method == "control":
            if args and args[0].get("subtype") == "checkpoint":
                return {"value": {"stdout": ""}}
            return {"tasks": []}
        if method == "ping":
            import hashlib

            from app.domain.agent.harness.claude_code.remote_execution import (
                launch,
                runtime,
            )

            return {
                "capabilities": ["prepare"],
                "protocol_version": runtime.PROTOCOL_VERSION,
                "files": {
                    name: hashlib.sha256(value.encode()).hexdigest()
                    for name, value in launch.file_sources().items()
                },
            }
        if method == "prepare":
            return target
        return {"device": target["device_id"]}

    remote = AsyncMock(side_effect=executor_answer)
    monkeypatch.setattr(execution, "call", remote)
    tokens = []
    room_device = identities[0][1]
    for session_id, _own_choice, token in identities:
        device = room_device
        response = client.post(
            f"/topics/{topic_id}/sessions/{session_id}/work-lease",
            headers={"X-Cheese-Token": token},
            json={"env": {}},
        )
        assert response.status_code == 200, response.text
        answer = response.json()["data"]
        assert answer["target"]["device_id"] == device
        tokens.append(answer["token"])
        response = client.post(
            f"/topics/{topic_id}/execution/{resource}",
            headers={"X-Cheese-Token": answer["token"]},
            json={
                "method": "invoke",
                "params": {"id": str(uuid.uuid4()), "tool": "Read"},
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["device"] == device
    assert hub.exec.await_count == 2
    # A session from before the address fix still has the wrong URL in its
    # persisted lease. Reacquiring work must repair it before the first ping.
    async with client.test_factory() as db:
        old_session = await AgentSessionService(db).by_id(identities[0][0])
        expected_url = old_session.work_lease["url"]
        old_session.work_lease = {
            **old_session.work_lease,
            "url": "http://172.17.0.1/topics/stale/execution/session-stale",
        }
        await db.commit()
    remote.reset_mock()
    for session_id, _device, token in identities:
        response = client.post(
            f"/topics/{topic_id}/sessions/{session_id}/work-lease",
            headers={"X-Cheese-Token": token},
            json={"env": {}},
        )
        assert response.status_code == 200, response.text
    assert hub.exec.await_count == 2, "A subsequent tool must reuse its session lease"
    assert remote.await_args_list[0].args[0]["url"] == expected_url
    assert any(call.args[1] == "prepare" for call in remote.await_args_list)
    # A deployment changes executor source files. The next tool updates the
    # existing resource; it does not leave an old executor running forever.
    remote.side_effect = lambda target, method, *args, **kwargs: (
        {"capabilities": [], "files": {}}
        if method == "ping"
        else executor_answer(target, method, *args, **kwargs)
    )
    upgraded = client.post(
        f"/topics/{topic_id}/sessions/{identities[0][0]}/work-lease",
        headers={"X-Cheese-Token": identities[0][2]},
        json={"env": {}},
    )
    assert upgraded.status_code == 200, upgraded.text
    assert hub.exec.await_count == 3
    assert upgraded.json()["data"]["target"]["device_id"] == identities[0][1]
    remote.side_effect = executor_answer
    # Both sessions of the room hold hands on the one machine, each in a
    # workspace of its own: one container per topic, one worktree per teammate.
    async with client.test_factory() as db:
        leases = [
            (await AgentSessionService(db).by_id(sid)).work_lease
            for sid, _, _ in identities
        ]
    assert [lease["device_id"] for lease in leases] == [room_device, room_device]
    assert leases[0]["resource_id"] != leases[1]["resource_id"]
    assert leases[0]["home"] != leases[1]["home"]
    wrong_session = client.post(
        f"/topics/{topic_id}/sessions/{identities[1][0]}/work-lease",
        headers={"X-Cheese-Token": identities[0][2]},
        json={"env": {}},
    )
    assert wrong_session.status_code == 403

    owner_headers = session_auth_headers("alice")
    first_id, first_device, first_token = identities[0]
    second_id, second_device, _ = identities[1]
    choice_path = f"/topics/{topic_id}/compute-profile"
    selection = {
        "choice": {
            "name": "Second machine",
            "profile": "device",
            "device_id": second_device,
        }
    }
    from app.domain.agent_instance.models import AgentInstance
    from app.domain.room_task.models import Task

    async with client.test_factory() as db:
        instance = AgentInstance(project_id=project_id, handle="ada", configuration={})
        db.add(instance)
        db.add(AgentInstance(project_id=project_id, handle="bob", configuration={}))
        await db.flush()
        first = await AgentSessionService(db).by_id(first_id)
        second = await AgentSessionService(db).by_id(second_id)
        first.resume_token, second.resume_token = "native-ada", "native-bob"
        child = Task(
            project_id=project_id,
            room_id=topic_id,
            title="Ada's background child",
            owner_handle="bob",
            subagent_id="ada-child",
            execution_agent_instance_id=instance.id,
            execution_parent_session_id="native-ada",
            execution_turn_id=uuid.uuid4(),
        )
        db.add(child)
        await db.flush()
        child_id = child.id
        await db.commit()
    bob_tools = client.post(
        f"/topics/{topic_id}/sessions/{second_id}/work-lease",
        headers={"X-Cheese-Token": identities[1][2]},
        json={"env": {}},
    )
    assert bob_tools.status_code == 200, bob_tools.text
    # A real execution timeout leaves NULL in the dispatch log. It is unknown,
    # not proof of an active call and must not permanently lock machine choice.
    remote.side_effect = TimeoutError("lost response")
    timed_out = client.post(
        f"/topics/{topic_id}/execution/{resource}",
        headers={"X-Cheese-Token": tokens[0]},
        json={"method": "invoke", "params": {"id": "lost-write", "tool": "Write"}},
    )
    assert timed_out.status_code == 504, timed_out.text
    async with client.test_factory() as db:
        dispatch = await db.scalar(
            select(DispatchRow).where(DispatchRow.key == "lost-write")
        )
        dispatch_id = dispatch.id
        assert dispatch.outcome is None
    hub.is_online = lambda device: old_online or device != first_device
    listing = client.get(
        f"/topics/{topic_id}/compute-profile", headers=owner_headers
    ).json()["data"]["sessions"]
    assert (
        next(item for item in listing if item["id"] == str(first_id))["lease"]["online"]
        is old_online
    )
    from app.domain.room_task.models import Task, TaskStatus

    async with client.test_factory() as db:
        db.add(
            Task(
                project_id=project_id,
                room_id=topic_id,
                title="Finished child",
                subagent_id="historical-child",
                status=TaskStatus.closed,
            )
        )
        db.add(
            Task(
                project_id=project_id,
                room_id=topic_id,
                title="Concluded child",
                subagent_id="concluded-child",
                conclusion="Handed back",
            )
        )
        await db.commit()
    # An agent may change its own work destination once its work is pushed on
    # the machine it leaves. Away from a machine that cannot be reached for
    # that, only a person may switch, and says so explicitly.
    from app.domain.agent.models import AgentTurn

    async with client.test_factory() as db:
        turn = AgentTurn(
            id=uuid.uuid4(),
            continuation_id=uuid.uuid4(),
            topic_id=topic_id,
            author=actor_handle,
            started_at=datetime.now(UTC),
        )
        db.add(turn)
        await db.commit()
    # An admitted synchronous call can finish on its retained old machine
    # while future tools move to the selected destination.
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    admitted, release = Event(), Event()

    async def finishing_call(target, method, params, **kwargs):
        if method == "ping":
            return running()
        if params.get("id") == "in-flight-read":
            admitted.set()
            await asyncio.to_thread(release.wait, 10)
        if method == "control":
            if background_state == "unresponsive":
                raise TimeoutError("old executor does not respond")
            assert params["subtype"] == "checkpoint"
            return {"value": {"stdout": ""}}
        return {"device": target["device_id"]}

    remote.side_effect = finishing_call
    with ThreadPoolExecutor(max_workers=1) as requests:
        pending_call = requests.submit(
            client.post,
            f"/topics/{topic_id}/execution/{resource}",
            headers={"X-Cheese-Token": tokens[0]},
            json={
                "method": "invoke",
                "params": {"id": "in-flight-read", "tool": "Read"},
            },
        )
        try:
            assert admitted.wait(5)
            async with client.test_factory() as db:
                first = await AgentSessionService(db).by_id(first_id)
                # A legacy recorded lease need not have a generation field.
                first.work_lease = {
                    key: value
                    for key, value in first.work_lease.items()
                    if key != "generation"
                }
                await db.commit()
            response = client.put(
                f"/topics/{topic_id}/compute-profile",
                headers={"X-Cheese-Token": first_token},
                json={"profile": "device", "device_id": second_device},
            )
            if old_online and background_state == "running":
                assert response.status_code == 200, response.text
            else:
                assert response.status_code == 409, response.text
                assert response.json()["error"]["name"] == "WorkComputerUnreachable"
                response = client.put(
                    choice_path,
                    headers=owner_headers,
                    json={**selection, "abandon_unpushed": True},
                )
                assert response.status_code == 200, response.text
        finally:
            release.set()
        finished = pending_call.result(timeout=10)
    assert finished.status_code == 200, finished.text
    assert finished.json()["device"] == first_device
    # The only thing the switch asks of the old machine is the push; it does
    # not wait for that machine's background work.
    assert all(
        call.args[2]["subtype"] == "checkpoint"
        for call in remote.await_args_list
        if call.args[1] == "control"
    )
    async with client.test_factory() as db:
        assert (await db.get(Task, child_id)).conclusion is None
    async with client.test_factory() as db:
        # 整个房间一起搬：发起的这一条和房间里的另一条都把手交了出来。
        for sid in (first_id, second_id):
            moved = await AgentSessionService(db).by_id(sid)
            assert moved.work_lease is None
            assert moved.execution_request["choice"]["device_id"] == second_device
        room_now = await db.get(Topic, topic_id)
        assert room_now.compute_config["device_id"] == second_device
    old_call = client.post(
        f"/topics/{topic_id}/execution/{resource}",
        headers={"X-Cheese-Token": tokens[0]},
        json={"method": "invoke", "params": {"id": "stale", "tool": "Write"}},
    )
    assert old_call.status_code == 409
    response = client.post(
        f"/topics/{topic_id}/sessions/{first_id}/work-lease",
        headers={"X-Cheese-Token": first_token},
        json={"env": {}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["target"]["device_id"] == second_device
    current_token = response.json()["data"]["token"]
    current_target = response.json()["data"]["target"]
    repeated = client.put(
        choice_path,
        headers={"X-Cheese-Token": first_token},
        json={
            "choice": {
                "name": "Renamed but same computer",
                "profile": "device",
                "device_id": second_device,
            }
        },
    )
    assert repeated.status_code == 200, repeated.text
    async with client.test_factory() as db:
        kept = await AgentSessionService(db).by_id(first_id)
        assert kept.work_lease["generation"] == current_target["generation"]
    assert (
        client.post(
            f"/topics/{topic_id}/execution/{resource}",
            headers={"X-Cheese-Token": tokens[0]},
            json={
                "method": "invoke",
                "params": {"id": "stale-after-replacement", "tool": "Write"},
            },
        ).status_code
        == 409
    )
    async with client.test_factory() as db:
        first = await AgentSessionService(db).by_id(first_id)
        assert (
            first.execution_request["retained_leases"][0]["device_id"] == first_device
        )
        assert (await db.get(DispatchRow, dispatch_id)).outcome is None

    async with client.test_factory() as db:
        await sql_device_service(db).unassign_from_project(
            second_device, project_id, actor_user_id=owner.id
        )
        await db.commit()
    revoked = client.post(
        f"/topics/{topic_id}/execution/{resource}",
        headers={"X-Cheese-Token": current_token},
        json={"method": "invoke", "params": {"id": "revoked", "tool": "Write"}},
    )
    assert revoked.status_code == 403

    # A session that joins the room later takes the room's choice with it.
    async with client.test_factory() as db:
        fresh = await AgentSessionService(db).ensure(
            topic_id, "first-tool-later", harness="claude-code"
        )
        fresh_id = fresh.id
        await db.commit()
    fresh_token = bind_resource_token(
        mint_scoped_token(
            project_id=str(project_id),
            topic_id=str(topic_id),
            agent_handle=actor_handle,
        ),
        resource,
        session_id=str(fresh_id),
    )
    hub.is_online = lambda _device: False
    waiting = client.post(
        f"/topics/{topic_id}/sessions/{fresh_id}/work-lease",
        headers={"X-Cheese-Token": fresh_token},
        json={"env": {}},
    )
    assert waiting.status_code == 200, waiting.text
    assert waiting.json()["data"]["unavailable"]


@pytest.mark.parametrize(
    "scenario", ["ping500", "old-release", "prepare-failure", "pending", "failed"]
)
async def test_lazy_executor_lifecycle_keeps_the_same_allocation(
    client, monkeypatch, scenario, tmp_path
):
    project = post_project(
        client, json={"name": "Session hands"}, owner="alice"
    ).json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    project_id, topic_id = uuid.UUID(project["id"]), uuid.UUID(room["id"])
    async with client.test_factory() as db:
        devices = sql_device_service(db)
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
        agent = await IdentityService(db).ensure_room_agent_user(topic_id)
        actor_handle = agent.username
        topic = await db.get(Topic, topic_id)
        resource = str(topic.resource_id or topic_id)
        identities = []
        for handle in ("ada", "bob"):
            code = await devices.start(handle)
            device = await devices.approve(
                code,
                owner_user_id=owner.id,
                supply=Supply.self_hosted,
                visibility=Visibility.host,
            )
            await devices.assign_to_project(
                device.device_id, project_id, actor_user_id=owner.id
            )
            session = await AgentSessionService(db).ensure(
                topic_id, handle, harness="claude-code"
            )
            session.execution_request = {
                "generation": str(uuid.uuid4()),
                "choice": {
                    "name": handle,
                    "profile": "device",
                    "device_id": device.device_id,
                },
            }
            session.runtime_location = {
                "device_id": "center",
                "resource_id": resource,
                "channel": "device",
            }
            token = bind_resource_token(
                mint_scoped_token(
                    project_id=str(project_id),
                    topic_id=str(topic_id),
                    agent_handle=actor_handle,
                ),
                resource,
                session_id=str(session.id),
            )
            identities.append((session.id, device.device_id, token))
            assert session.work_lease is None
        await db.commit()

    import hashlib

    import httpx

    from app.domain.agent.harness.claude_code import executor_launch
    from app.domain.agent.harness.claude_code.remote_execution import runtime

    launch_env = {}
    launch_options = {}
    original_script = executor_launch.script

    def capture_script(project, resource, env, **options):
        launch_env.update(env)
        launch_options.update(options)
        return original_script(project, resource, env, **options)

    monkeypatch.setattr(executor_launch, "script", capture_script)
    info = {
        "state": "/executor/state",
        "workspace": "/executor/work",
        "mcp_servers": [],
    }
    hub = SimpleNamespace(
        is_online=lambda _: True,
        exec=AsyncMock(return_value={"exit": 0, "stdout": json.dumps(info)}),
    )
    monkeypatch.setattr(work_lease, "device_hub", hub)
    state = {"phase": "initial"}

    async def execute(target, method, params, **kwargs):
        if method == "ping":
            if state["phase"] == "ping500":
                state["phase"] = "restarted"
                response = httpx.Response(
                    500, request=httpx.Request("POST", "http://owner")
                )
                raise httpx.HTTPStatusError(
                    "stopped", request=response.request, response=response
                )
            if state["phase"] == "old-release":
                return {"capabilities": ["prepare"], "files": {}}
            return {
                "capabilities": ["prepare"],
                "protocol_version": runtime.PROTOCOL_VERSION,
                "files": {
                    name: hashlib.sha256(value.encode()).hexdigest()
                    for name, value in executor_launch.file_sources().items()
                },
            }
        if method == "prepare":
            if state["phase"] == "prepare-failure":
                raise RuntimeError("prepare rejected")
            return info
        raise AssertionError(method)

    monkeypatch.setattr(execution, "call", AsyncMock(side_effect=execute))
    status = AsyncMock(return_value={"state": "ready"})
    monkeypatch.setattr(work_lease, "environment_status", status)
    session_id, _, token = identities[0]
    path = f"/topics/{topic_id}/sessions/{session_id}/work-lease"
    body = {"env": {"CHEESE_ENVIRONMENT": '{"revision":"one"}'}}
    first = client.post(path, headers={"X-Cheese-Token": token}, json=body)
    assert first.status_code == 200, first.text
    assert launch_env["CHEESE_PREVIEW_URL"].startswith(("ws://", "wss://"))
    assert launch_env["CHEESE_PREVIEW_URL"].endswith("/preview/tunnel")
    if scenario == "ping500":
        import os
        import subprocess
        import time

        from app.domain.agent.machine_launcher import CHEESE_PREVIEW_UP

        home = tmp_path / "preview-home"
        helper = home / ".cheese/cheese-preview.py"
        helper.parent.mkdir(parents=True)
        helper.write_text(
            "import json, pathlib, sys\n"
            "pathlib.Path(__file__).with_suffix('.args').write_text(json.dumps(sys.argv[1:]))\n"
        )
        subprocess.run(
            ["sh", "-c", CHEESE_PREVIEW_UP, "preview", "8080"],
            env={**os.environ, **launch_env, "HOME": str(home)},
            check=True,
            timeout=5,
        )
        recorded = helper.with_suffix(".args")

        def written() -> list[str] | None:
            # The helper runs detached and writes its arguments in one go, but
            # the file exists, empty, a moment before they land in it.
            try:
                return json.loads(recorded.read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                return None

        deadline = time.monotonic() + HANG_S
        while (args := written()) is None:
            assert time.monotonic() < deadline, "the preview helper never started"
            time.sleep(0.01)
        assert args[args.index("--url") + 1] == launch_env["CHEESE_PREVIEW_URL"]
    # A self-hosted machine's sessions see it whole: no sandbox yet (#2320).
    assert launch_options == {"sandbox": False}
    assert launch_env["CHEESE_AUTHOR"] == actor_handle
    assert launch_env["GIT_AUTHOR_NAME"] == actor_handle
    assert launch_env["GIT_AUTHOR_EMAIL"] == f"{actor_handle}@agent.cheese.local"
    resource_id = first.json()["data"]["target"]["resource_id"]
    assert hub.exec.await_count == 1
    async with client.test_factory() as db:
        current = await AgentSessionService(db).by_id(session_id)
        before = dict(current.work_lease)
        current.work_lease = {
            **before,
            "status": "preparing",
            "claim": str(uuid.uuid4()),
            "claim_until": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
        }
        await db.commit()
    # Another request of this session holds the installation: this one waits
    # for it and takes what it installed.
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as requests:
        pending_tool = requests.submit(
            client.post, path, headers={"X-Cheese-Token": token}, json=body
        )
        await asyncio.sleep(1.5)
        assert not pending_tool.done(), "The other installer is still preparing"
        async with client.test_factory() as db:
            current = await AgentSessionService(db).by_id(session_id)
            current.work_lease = before
            await db.commit()
        reserved = pending_tool.result(timeout=20)
    assert reserved.status_code == 200, reserved.text
    assert reserved.json()["data"]["target"]["resource_id"] == before["resource_id"]
    assert hub.exec.await_count == 1, "Another installer owns this reservation"
    state["phase"] = scenario
    monkeypatch.setattr(work_lease, "ENVIRONMENT_POLL_S", 0.2)
    if scenario == "pending":
        # Still being prepared: the tool waits until it is ready.
        status.side_effect = [
            {"state": "preparing"},
            {"state": "preparing"},
            {"state": "ready"},
        ]
    if scenario == "failed":
        status.return_value = {"state": scenario, "error": "environment not ready"}
    if scenario == "prepare-failure":
        with pytest.raises(RuntimeError, match="prepare rejected"):
            client.post(path, headers={"X-Cheese-Token": token}, json=body)
        assert hub.exec.await_count == 1, (
            "A failed prepare must not be retried as installation"
        )
    else:
        next_tool = client.post(path, headers={"X-Cheese-Token": token}, json=body)
        assert next_tool.status_code == 200, next_tool.text
        if scenario == "pending":
            assert next_tool.json()["data"]["target"]["resource_id"] == resource_id
            assert status.await_count >= 3
            assert hub.exec.await_count == 1
        elif scenario == "failed":
            # A failed environment will not become ready by waiting.
            assert next_tool.json()["data"]["environment_status"]["state"] == scenario
            assert "target" not in next_tool.json()["data"]
            assert "preparing" not in next_tool.json()["data"]
            assert hub.exec.await_count == 1
            status.return_value = {"state": "ready"}
            retry = client.post(path, headers={"X-Cheese-Token": token}, json=body)
            assert retry.json()["data"]["target"]["resource_id"] == resource_id
            assert hub.exec.await_count == 1
        else:
            assert hub.exec.await_count == 2
            assert next_tool.json()["data"]["target"]["resource_id"] == resource_id
    async with client.test_factory() as db:
        current = await AgentSessionService(db).by_id(session_id)
        assert current.work_lease["resource_id"] == resource_id


async def test_the_rooms_agent_may_choose_cloud(client, monkeypatch):
    """Being on the project is enough; the agent need not sit on the team itself."""
    from app.domain.agent import compute_configs
    from app.domain.project.models import Project
    from app.domain.team.models import Team

    project = post_project(
        client, json={"name": "Cloud authority"}, owner="alice"
    ).json()["data"]
    room = client.post(
        "/topics",
        json={
            "project_id": project["id"],
            "title": "Cloud room",
        },
        headers=session_auth_headers("alice"),
    ).json()["data"]
    project_id, topic_id = uuid.UUID(project["id"]), uuid.UUID(room["id"])
    async with client.test_factory() as db:
        agent = await IdentityService(db).ensure_room_agent_user(topic_id)
        actor_handle, actor_id = agent.username, agent.id
        row = await AgentSessionService(db).ensure(
            topic_id, actor_handle, harness="claude-code"
        )
        session_id = row.id
        topic = await db.get(Topic, topic_id)
        token = bind_resource_token(
            mint_scoped_token(
                project_id=str(project_id),
                topic_id=str(topic_id),
                agent_handle=actor_handle,
            ),
            str(topic.resource_id or topic_id),
            session_id=str(session_id),
        )
        team = Team(
            name="Compute team",
            handle=f"t-{uuid.uuid4().hex[:12]}",
            intro="",
            description="",
            avatar_id=1,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.add(team)
        await db.flush()
        team_id = team.id
        (await db.get(Project, project_id)).team_id = team_id
        await db.commit()
    monkeypatch.setattr(compute_configs, "cloud_provisionable", lambda settings: True)
    # 房间那条路由也问池接没接入（``compute_selectable``）。
    from app.domain.agent import market

    monkeypatch.setattr(market, "cloud_provisionable", lambda settings: True)
    # 会话凭据改的是房间（一个话题一个容器）。
    path = f"/topics/{topic_id}/compute-profile"
    body = {"choice": {"name": "Cloud", "profile": "cloud"}}
    headers = {"X-Cheese-Token": token}
    selected = client.put(path, headers=headers, json=body)
    assert selected.status_code == 200, selected.text
    assert selected.json()["data"]["choice"]["profile"] == "cloud"
    async with client.test_factory() as db:
        row = await AgentSessionService(db).by_id(session_id)
        assert row.execution_request["choice"]["profile"] == "cloud"
        assert row.work_lease is None
        assert row.execution_request["authorized_by"]["via"] == "cheese"
        assert row.execution_request["authorized_by"]["handle"] == actor_handle

    # Cloud provisioning may already own a VM while work_lease is still empty.
    # Changing its specification must retire that allocation rather than reuse it.
    from app.domain.machine.models import MachineStatus
    from app.domain.machine.repositories import ProjectMachineRepository

    async with client.test_factory() as db:
        repo = ProjectMachineRepository(db)
        old_machine = await repo.add(
            project_id=project_id,
            topic_id=topic_id,
            session_id=session_id,
            machine_id=71,
            customer_id=7,
            account_id=9,
            offering_id=1,
            hostname="first-cloud",
            login_user="cheese",
            cores=2,
            memory_mb=4096,
            disk_gb=20,
            status=MachineStatus.running,
            ip=None,
            requested_by=actor_handle,
        )
        old_machine_id = old_machine.id
        await db.commit()
    changed = client.put(
        path,
        headers=headers,
        json={"choice": {"name": "Larger cloud", "profile": "cloud", "cores": 4}},
    )
    assert changed.status_code == 200, changed.text
    async with client.test_factory() as db:
        repo = ProjectMachineRepository(db)
        assert (await repo.get(old_machine_id)).superseded_at is not None
        pending = await repo.add(
            project_id=project_id,
            topic_id=topic_id,
            session_id=session_id,
            machine_id=None,
            customer_id=7,
            account_id=9,
            offering_id=1,
            hostname="pending-cloud",
            login_user="cheese",
            cores=4,
            memory_mb=4096,
            disk_gb=20,
            status=MachineStatus.running,
            ip=None,
            requested_by=actor_handle,
        )
        pending_id = pending.id
        devices = sql_device_service(db)
        code = await devices.start("Available work computer")
        device = await devices.approve(
            code,
            owner_user_id=actor_id,
            supply=Supply.self_hosted,
            visibility=Visibility.host,
        )
        await devices.assign_to_project(
            device.device_id,
            project_id,
            actor_user_id=actor_id,
        )
        device_id = device.device_id
        await db.commit()
    replacement = {
        "choice": {
            "name": "Work computer",
            "profile": "device",
            "device_id": device_id,
        }
    }
    blocked = client.put(path, headers=headers, json=replacement)
    assert blocked.status_code == 409, blocked.text
    async with client.test_factory() as db:
        repo = ProjectMachineRepository(db)
        pending = await repo.get(pending_id)
        assert pending.superseded_at is None
        pending.machine_id = 72
        await db.commit()
    switched = client.put(path, headers=headers, json=replacement)
    assert switched.status_code == 200, switched.text
    async with client.test_factory() as db:
        repo = ProjectMachineRepository(db)
        assert (await repo.get(pending_id)).superseded_at is not None
        row = await AgentSessionService(db).by_id(session_id)
        assert row.work_lease is None
        assert row.execution_request["choice"]["device_id"] == device_id


async def test_each_dialer_gets_its_configured_base_not_the_request_host(
    client, monkeypatch
):
    """The session host dials the lease url; the work device dials the setup env.

    Neither is the Host header of the hop that delivered the lease request.
    """
    from app.core.config import settings
    from app.domain.agent.harness.claude_code import executor_launch

    monkeypatch.setattr(settings, "agent_session_device_id", "center")
    monkeypatch.setattr(settings, "agent_session_api_base", "http://172.17.0.1:8081/")
    monkeypatch.setattr(
        settings, "connector_public_base", "https://cheese.example.test/api"
    )
    project = post_project(client, json={"name": "Dialers"}, owner="alice").json()[
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
        agent = await IdentityService(db).ensure_room_agent_user(topic_id)
        topic = await db.get(Topic, topic_id)
        resource = str(topic.resource_id or topic_id)
        device = await devices.approve(
            await devices.start("worker"),
            owner_user_id=owner.id,
            supply=Supply.self_hosted,
            visibility=Visibility.host,
        )
        await devices.assign_to_project(
            device.device_id, project_id, actor_user_id=owner.id
        )
        session = await AgentSessionService(db).ensure(
            topic_id, "ada", harness="claude-code"
        )
        session.execution_request = {
            "generation": str(uuid.uuid4()),
            "choice": {
                "name": "worker",
                "profile": "device",
                "device_id": device.device_id,
            },
        }
        session.runtime_location = {
            "device_id": "center",
            "resource_id": resource,
            "channel": "device",
        }
        token = bind_resource_token(
            mint_scoped_token(
                project_id=str(project_id),
                topic_id=str(topic_id),
                agent_handle=agent.username,
            ),
            resource,
            session_id=str(session.id),
        )
        session_id = session.id
        await db.commit()

    launch_env = {}
    original_script = executor_launch.script

    def capture_script(project, resource, env, **options):
        launch_env.update(env)
        return original_script(project, resource, env, **options)

    monkeypatch.setattr(executor_launch, "script", capture_script)
    info = {"state": "/w/state", "workspace": "/w/work", "mcp_servers": []}
    hub = SimpleNamespace(
        is_online=lambda _: True,
        exec=AsyncMock(return_value={"exit": 0, "stdout": json.dumps(info)}),
    )
    monkeypatch.setattr(work_lease, "device_hub", hub)
    monkeypatch.setattr(execution, "call", AsyncMock(return_value={}))

    response = client.post(
        f"/topics/{topic_id}/sessions/{session_id}/work-lease",
        headers={"X-Cheese-Token": token, "Host": "172.17.0.1"},
        json={"env": {}},
    )
    assert response.status_code == 200, response.text
    target = response.json()["data"]["target"]
    assert target["url"] == (
        f"http://172.17.0.1:8081/topics/{topic_id}/execution/session-{resource}"
    )
    assert launch_env["CHEESE_API"] == "https://cheese.example.test/api"
    assert launch_env["CHEESE_PREVIEW_URL"] == (
        "wss://cheese.example.test/api/preview/tunnel"
    )


async def test_calls_on_held_hands_are_answered_while_they_are_rechecked(
    client, monkeypatch
):
    """A session re-checks the hands it holds at every command start and file
    tool, while its other calls — parallel tools, subagents, a running command
    being read — are in flight on them. Those calls reach the machine; they are
    not refused as belonging to a generation that is no longer current."""
    import asyncio
    import hashlib
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from app.domain.agent.harness.claude_code import executor_launch
    from app.domain.agent.harness.claude_code.remote_execution import runtime

    project = post_project(
        client, json={"name": "Session hands"}, owner="alice"
    ).json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    project_id, topic_id = uuid.UUID(project["id"]), uuid.UUID(room["id"])
    async with client.test_factory() as db:
        devices = sql_device_service(db)
        owner = await db.scalar(select(User).where(User.username == "alice"))
        agent = await IdentityService(db).ensure_room_agent_user(topic_id)
        topic = await db.get(Topic, topic_id)
        resource = str(topic.resource_id or topic_id)
        device = await devices.approve(
            await devices.start("ada"),
            owner_user_id=owner.id,
            supply=Supply.self_hosted,
            visibility=Visibility.host,
        )
        await devices.assign_to_project(
            device.device_id, project_id, actor_user_id=owner.id
        )
        topic.compute_config = {
            "name": "ada",
            "profile": "device",
            "device_id": device.device_id,
        }
        session = await AgentSessionService(db).ensure(
            topic_id, "cheese", harness="claude-code"
        )
        session.runtime_location = {
            "device_id": "center",
            "resource_id": resource,
            "channel": "device",
        }
        token = bind_resource_token(
            mint_scoped_token(
                project_id=str(project_id),
                topic_id=str(topic_id),
                agent_handle=agent.username,
            ),
            resource,
            session_id=str(session.id),
        )
        session_id = session.id
        await db.commit()

    info = {"state": "/executor/state", "workspace": "/work", "mcp_servers": []}
    hub = SimpleNamespace(
        is_online=lambda _: True,
        exec=AsyncMock(return_value={"exit": 0, "stdout": json.dumps(info)}),
    )
    monkeypatch.setattr(work_lease, "device_hub", hub)
    checking, release = threading.Event(), threading.Event()
    hold = {"next_ping": False}

    async def execute(target, method, params, **kwargs):
        if method == "ping":
            if hold["next_ping"]:
                hold["next_ping"] = False
                checking.set()
                while not release.is_set():
                    await asyncio.sleep(0.01)
            return {
                "capabilities": ["prepare"],
                "protocol_version": runtime.PROTOCOL_VERSION,
                "files": {
                    name: hashlib.sha256(value.encode()).hexdigest()
                    for name, value in executor_launch.file_sources().items()
                },
            }
        if method == "prepare":
            return info
        return {"ran": method}

    monkeypatch.setattr(execution, "call", AsyncMock(side_effect=execute))
    lease_path = f"/topics/{topic_id}/sessions/{session_id}/work-lease"
    first = client.post(lease_path, headers={"X-Cheese-Token": token}, json={})
    assert first.status_code == 200, first.text
    execution_token = first.json()["data"]["token"]

    hold["next_ping"] = True
    with ThreadPoolExecutor(1) as pool:
        recheck = pool.submit(
            client.post, lease_path, headers={"X-Cheese-Token": token}, json={}
        )
        assert checking.wait(10), "the second acquire never re-checked the hands"
        try:
            during = client.post(
                f"/topics/{topic_id}/execution/{resource}",
                headers={"X-Cheese-Token": execution_token},
                json={"method": "control", "params": {"subtype": "shell"}},
            )
        finally:
            release.set()
        assert recheck.result(10).status_code == 200
    assert during.status_code == 200, during.text
    assert during.json() == {"ran": "control"}


async def test_an_own_machine_that_just_dropped_is_called_not_refused(
    client, monkeypatch
):
    """A person's own machine whose link dropped a moment ago is on its way
    back: the command goes to it and waits there, rather than being answered
    「工作电脑未连接」 on the spot. One that does not come back in time is then
    answered as not connected, like one already away."""
    project = post_project(client, json={"name": "Own machine"}, owner="alice").json()[
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
        owner = await db.scalar(select(User).where(User.username == "alice"))
        agent = await IdentityService(db).ensure_room_agent_user(topic_id)
        topic = await db.get(Topic, topic_id)
        resource = str(topic.resource_id or topic_id)
        device = await devices.approve(
            await devices.start("laptop"),
            owner_user_id=owner.id,
            supply=Supply.self_hosted,
            visibility=Visibility.host,
        )
        await devices.assign_to_project(
            device.device_id, project_id, actor_user_id=owner.id
        )
        topic.compute_config = {
            "name": "laptop",
            "profile": "device",
            "device_id": device.device_id,
        }
        session = await AgentSessionService(db).ensure(
            topic_id, "cheese", harness="claude-code"
        )
        session.runtime_location = {
            "device_id": "center",
            "resource_id": resource,
            "channel": "device",
        }
        session_id, device_id = session.id, device.device_id
        token = bind_resource_token(
            mint_scoped_token(
                project_id=str(project_id),
                topic_id=str(topic_id),
                agent_handle=agent.username,
            ),
            resource,
            session_id=str(session_id),
        )
        await db.commit()

    async def install(device, argv, **kwargs):
        return {
            "exit": 0,
            "stdout": json.dumps(
                {"state": f"/{device}/state", "workspace": "/w", "mcp_servers": []}
            ),
        }

    hub = SimpleNamespace(
        is_online=lambda _device: False,
        reconnecting=lambda _device: True,
        exec=AsyncMock(side_effect=DeviceOffline(device_id)),
    )
    monkeypatch.setattr(work_lease, "device_hub", hub)
    monkeypatch.setattr(execution, "call", AsyncMock(return_value={}))

    def lease():
        return client.post(
            f"/topics/{topic_id}/sessions/{session_id}/work-lease",
            headers={"X-Cheese-Token": token},
            json={"env": {}},
        )

    gone = lease()
    assert gone.status_code == 200, gone.text
    assert gone.json()["data"]["unavailable"]
    assert hub.exec.await_count == 1, "The call went to the machine"

    hub.exec.side_effect = install
    back = lease()
    assert back.status_code == 200, back.text
    assert back.json()["data"]["target"]["device_id"] == device_id
