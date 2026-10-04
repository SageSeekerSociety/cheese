"""Topic compute-profile API (execution-architecture v4 会话级选择).

A room's choice is what an agent gets when it starts working there: before the
first turn it is also the room's pin, and afterwards it is the default for the
agents that start later. Each session that has started keeps its own machine.
"""

import asyncio
import uuid
from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.compute_configs import (
    ComputeChoice,
    ProjectComputeConfigs,
    standard_choice,
)
from app.domain.agent.device_hub import device_hub
from app.domain.agent.device_provider import resolve_pinned_device
from app.domain.agent.harness.channel import ScreenSetupError
from app.domain.agent.platform_failures import DEVICE_OFFLINE_MESSAGE
from app.domain.agent_session.repositories import AgentSessionRepository
from app.domain.agent_session.services import AgentSessionService
from app.domain.device.supply import Supply, Visibility
from app.domain.device.wiring import sql_device_service
from app.domain.identity.handles import CHEESE_HANDLE
from app.domain.machine.services import MachineService
from app.domain.project.models import Project
from tests.executor_release import running
from tests.integration.conftest import post_project, session_auth_headers


def _project(client, owner: str = "andyl") -> str:
    return post_project(client, json={"name": "P"}, owner=owner).json()["data"]["id"]


def _topic(client, pid: str) -> str:
    return client.post(
        "/topics",
        json={"project_id": pid, "title": "T"},
        headers=session_auth_headers("andyl"),
    ).json()["data"]["id"]


def _mark_started(client, tid: str) -> None:
    """Simulate the topic having run one turn (a session captured)."""

    async def _run() -> None:
        async with client.test_factory() as s:
            await AgentSessionRepository(s).save(
                conversation_id=uuid.UUID(tid),
                agent_handle=CHEESE_HANDLE,
                resume_token="sess-1",
                harness="claude-code",
            )
            await s.commit()

    asyncio.run(_run())


def _project_devices(client, pid: str, *names: str) -> list[str]:
    async def _seed() -> list[str]:
        async with client.test_factory() as session:
            service = sql_device_service(session)
            device_ids: list[str] = []
            for name in names:
                code = await service.start(name)
                device = await service.approve(
                    code,
                    owner_user_id=1,
                    supply=Supply.self_hosted,
                )
                await service.assign_to_project(
                    device.device_id, uuid.UUID(pid), actor_user_id=1
                )
                device_ids.append(device.device_id)
            await session.commit()
            return device_ids

    return asyncio.run(_seed())


def _agent_seat(client, tid: str) -> str:
    rows = client.get(f"/topics/{tid}/members").json()["data"]["data"]
    seats = [m["member_handle"] for m in rows if m["agent"]]
    assert len(seats) == 1, seats
    return seats[0]


def _topic_binding(client, tid: str):
    async def _read():
        async with client.test_factory() as session:
            return await sql_device_service(session).topic_binding(uuid.UUID(tid))

    return asyncio.run(_read())


def _resolve_topic_device(
    client,
    pid: str,
    tid: str,
    is_online: Callable[[str], bool],
) -> str | None:
    async def _resolve() -> str | None:
        async with client.test_factory() as session:
            return await resolve_pinned_device(
                sql_device_service(session),
                is_online,
                uuid.UUID(pid),
                uuid.UUID(tid),
            )

    return asyncio.run(_resolve())


def test_new_topic_inherits_default_and_has_no_sessions(client):
    pid = _project(client)
    tid = _topic(client, pid)
    from app.core.config import settings
    from app.domain.agent.market import compute_default_name

    body = client.get(f"/topics/{tid}/compute-profile").json()["data"]
    # Nothing selected anywhere, so the deployment's own fallback applies — never
    # the retired local pool (#358). Last selection would win if there were one.
    assert body["current"] == compute_default_name(settings) == "device"
    assert body["sessions"] == []
    # ...and the retired pool is no longer offered as a choice.
    assert "local-docker" not in {p["id"] for p in body["profiles"]}


async def _online(*_args, **_kwargs) -> bool:
    return True


def test_select_persists_only_to_topic(client, monkeypatch):
    pid = _project(client)
    tid = _topic(client, pid)
    # `device` is the carrier: local-docker is retired (#358) and Cloud demands a
    # verified human caller (it provisions a billed VM — see the authorization test
    # below). This test covers room-local profile selection.
    # The handler moved into topics_compute.py, so the name it reads lives there.
    monkeypatch.setattr("app.api.routes.topics_compute.project_device_online", _online)

    # An undeployed pool can't be selected.
    bad = client.put(f"/topics/{tid}/compute-profile", json={"profile": "gpu"})
    assert bad.status_code == 422
    # A retired one cannot either — no silent fallback to it.
    retired = client.put(
        f"/topics/{tid}/compute-profile", json={"profile": "local-docker"}
    )
    assert retired.status_code == 422

    r = client.put(f"/topics/{tid}/compute-profile", json={"profile": "device"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["current"] == "device"

    # Persisted on the topic...
    tbody = client.get(f"/topics/{tid}/compute-profile").json()["data"]
    assert tbody["choice"]["profile"] == "device"
    # The project's default is unchanged.
    pbody = client.get(f"/projects/{pid}/compute-configs").json()["data"]
    assert pbody["default"]["profile"] == "device"


def test_changing_the_room_while_an_agent_is_getting_its_machine_does_not_deadlock(
    client, monkeypatch
):
    """An agent asking for its machine locks the room, then the room's machine
    slot. A person changing the room at that moment must wait behind it, not
    take the slot first and then wait for the room: that pair of waits is a
    deadlock, and one of the two requests failed with a 500 on dev."""
    import threading

    from app.domain.machine.repositories import ProjectMachineRepository
    from app.domain.topic.services import TopicService

    pid = _project(client)
    tid = _topic(client, pid)
    monkeypatch.setattr("app.api.routes.topics_compute.project_device_online", _online)
    room_held = threading.Event()
    change_sent = threading.Event()
    agent_failure: list[BaseException] = []

    async def agent_getting_its_machine() -> None:
        async with client.test_factory() as session:
            await TopicService(session).lock_for_execution(uuid.UUID(tid))
            room_held.set()
            await asyncio.to_thread(change_sent.wait, 10)
            # Long enough for the change to reach whichever lock it waits on.
            await asyncio.sleep(1.5)
            await ProjectMachineRepository(session).lock_topic(uuid.UUID(tid))
            await session.commit()

    def run_agent() -> None:
        try:
            asyncio.run(agent_getting_its_machine())
        except BaseException as exc:  # noqa: BLE001 - reported to the test body
            agent_failure.append(exc)

    agent = threading.Thread(target=run_agent)
    agent.start()
    assert room_held.wait(10)
    change_sent.set()
    response = client.put(f"/topics/{tid}/compute-profile", json={"profile": "device"})
    agent.join(20)

    assert agent_failure == []
    assert response.status_code == 200, response.text


def test_named_device_resolves_instead_of_first_healthy_device(client, monkeypatch):
    pid = _project(client)
    tid = _topic(client, pid)
    first, named = _project_devices(client, pid, "online first", "named machine")
    monkeypatch.setattr(device_hub, "is_online", lambda _device_id: True)

    response = client.put(
        f"/topics/{tid}/compute-profile",
        json={"profile": "device", "device_id": named},
    )

    assert response.status_code == 200
    assert response.json()["data"]["device_id"] == named
    assert _topic_binding(client, tid).device_id == named
    assert first != named


def test_named_device_waits_when_offline_instead_of_using_online_peer(
    client, monkeypatch
):
    pid = _project(client)
    tid = _topic(client, pid)
    online_device, named_offline_device = _project_devices(
        client, pid, "online first", "named but offline"
    )
    monkeypatch.setattr(
        device_hub, "is_online", lambda device_id: device_id == online_device
    )

    response = client.put(
        f"/topics/{tid}/compute-profile",
        json={"profile": "device", "device_id": named_offline_device},
    )

    assert response.status_code == 200
    assert response.json()["data"]["device_id"] == named_offline_device
    binding = _topic_binding(client, tid)
    assert binding.device_id == named_offline_device
    assert binding.visibility is Visibility.isolated
    with pytest.raises(ScreenSetupError) as excinfo:
        _resolve_topic_device(
            client,
            pid,
            tid,
            lambda device_id: device_id == online_device,
        )
    assert str(excinfo.value) == DEVICE_OFFLINE_MESSAGE
    assert _topic_binding(client, tid).device_id == named_offline_device


def test_device_outside_topics_project_is_rejected(client):
    topic_pid = _project(client)
    other_pid = _project(client)
    tid = _topic(client, topic_pid)
    (other_device,) = _project_devices(client, other_pid, "somebody else's box")

    response = client.put(
        f"/topics/{tid}/compute-profile",
        json={"profile": "device", "device_id": other_device},
    )

    assert response.status_code == 422
    assert "不属于当前项目" in response.json()["message"]
    assert _topic_binding(client, tid) is None


def test_get_lists_only_project_devices_with_live_online_state(client, monkeypatch):
    pid = _project(client)
    other_pid = _project(client)
    tid = _topic(client, pid)
    office, home = _project_devices(client, pid, "办公室 Mac mini", "家里那台")
    (outside,) = _project_devices(client, other_pid, "别人的机器")
    monkeypatch.setattr(device_hub, "is_online", lambda device_id: device_id == office)

    body = client.get(f"/topics/{tid}/compute-profile").json()["data"]

    assert body["device_id"] is None
    common = {"owned": False, "sandbox_unavailable": None}
    assert body["devices"] == [
        {"device_id": office, "name": "办公室 Mac mini", "online": True, **common},
        {"device_id": home, "name": "家里那台", "online": False, **common},
    ]
    assert outside not in {device["device_id"] for device in body["devices"]}


def test_a_started_room_takes_its_pin_along(client):
    """一个话题一个容器（2026-09-28，推翻结论 60）：开工之后改房间这一项，就是整个
    房间搬过去，钉子跟着搬。"""
    pid = _project(client)
    tid = _topic(client, pid)
    first, second = _project_devices(client, pid, "first", "second")

    assert (
        client.put(
            f"/topics/{tid}/compute-profile",
            json={"profile": "device", "device_id": second},
        ).status_code
        == 200
    )
    assert _topic_binding(client, tid).device_id == second

    _mark_started(client, tid)
    changed = client.put(
        f"/topics/{tid}/compute-profile",
        json={"profile": "device", "device_id": first},
    )

    assert changed.status_code == 200, changed.text
    body = client.get(f"/topics/{tid}/compute-profile").json()["data"]
    assert body["choice"]["device_id"] == first
    assert _topic_binding(client, tid).device_id == first


def test_changing_the_room_moves_every_agent_already_working(client, monkeypatch):
    """房间里两位队友都在「这里」干活；房间换到「那里」，两位都跟着搬——各自先在
    离开的那台上推送。"""
    from app.domain.agent import execution

    monkeypatch.setattr(device_hub, "is_online", lambda _device_id: True)
    pushes = []

    async def push(target, method, params, **_kwargs):
        if method == "ping":
            return running()
        if method == "control":
            pushes.append((target["device_id"], method, params.get("subtype")))
        return {"value": {"stdout": "", "stderr": "", "interrupted": False}}

    monkeypatch.setattr(execution, "call", push)
    pid = _project(client)
    tid = _topic(client, pid)
    here, there = _project_devices(client, pid, "here", "there")
    lease = {
        "kind": "device",
        "device_id": here,
        "status": "ready",
        "home": "/here",
        "state": "/here/state",
    }
    working = {
        _session(
            client,
            tid,
            handle,
            choice=ComputeChoice(name="here", profile="device", device_id=here),
            lease=lease,
        )
        for handle in ("analyst", "writer")
    }

    changed = client.put(
        f"/topics/{tid}/compute-profile",
        json={"profile": "device", "device_id": there},
    )

    assert changed.status_code == 200, changed.text
    assert pushes == [(here, "control", "checkpoint")] * 2
    body = client.get(f"/topics/{tid}/compute-profile").json()["data"]
    assert body["choice"]["device_id"] == there
    assert {s["id"] for s in body["sessions"]} == working
    for session in body["sessions"]:
        assert session["choice"]["device_id"] == there
        assert session["lease"] is None


def test_selecting_cloud_without_machine_create_authority_is_refused(
    client, monkeypatch
):
    pid = _project(client)
    tid = _topic(client, pid)
    monkeypatch.setattr(settings, "microcloud_base_url", "https://cloud.example")
    monkeypatch.setattr(settings, "microcloud_tenant_secret", "secret")
    provision = AsyncMock()
    monkeypatch.setattr(MachineService, "provision", provision)

    response = client.put(f"/topics/{tid}/compute-profile", json={"profile": "cloud"})

    assert response.status_code == 401
    provision.assert_not_awaited()


def test_visibility_block_is_present(client):
    """#282 §四: the compute-profile response a room reads carries the
    visibility 档 so the room can SHOW whether a turn sees the whole machine.

    Both run; the default is the isolated one (#2320), and the whole machine
    is something an owner gives. A room on Cloud has no agent on an enrolled
    machine, so `machine_access` is False."""
    pid = _project(client)
    tid = _topic(client, pid)
    _project_default(client, pid, standard_choice("cloud"))
    vis = client.get(f"/topics/{tid}/compute-profile").json()["data"]["visibility"]

    opts = {o["id"]: o for o in vis["options"]}
    assert opts["isolated"]["available"] is True
    assert opts["isolated"]["default"] is True
    assert opts["host"]["available"] is True
    assert opts["host"]["default"] is False
    assert "整台机器" in opts["host"]["description"]
    defaults = [o for o in vis["options"] if o["default"]]
    assert len(defaults) == 1 and defaults[0]["available"] is True

    assert vis["effective"] is None
    assert vis["machine_access"] is False


def _project_default(client, pid: str, choice: ComputeChoice) -> None:
    async def _write() -> None:
        async with client.test_factory() as session:
            project = await session.get(Project, uuid.UUID(pid))
            project.settings = {
                **(project.settings or {}),
                "compute_configs": ProjectComputeConfigs(default=choice).model_dump(),
            }
            await session.commit()

    asyncio.run(_write())


def _session(client, tid: str, handle: str, *, choice=None, lease=None) -> str:
    """An agent session in the room, holding a choice and possibly a lease."""

    async def _write() -> str:
        async with client.test_factory() as session:
            row = await AgentSessionService(session).ensure(
                uuid.UUID(tid), handle, harness="claude-code"
            )
            if choice is not None:
                row.execution_request = {
                    "generation": str(uuid.uuid4()),
                    "choice": choice.model_dump(),
                    "authorized_by": None,
                }
            row.work_lease = lease
            await session.commit()
            return str(row.id)

    return asyncio.run(_write())


def _visibility(client, tid: str) -> dict:
    return client.get(f"/topics/{tid}/compute-profile").json()["data"]["visibility"]


def test_a_room_that_let_the_system_pick_an_enrolled_machine_runs_isolated(client):
    """「系统挑一台」的房间没有钉子，房间里的手已经站在那台登记过的机器上：没有
    人给过它整台机器，所以它在那台上是隔离环境（#2320），不显示整机提醒。"""
    pid = _project(client)
    tid = _topic(client, pid)
    (lab,) = _project_devices(client, pid, "lab")
    _project_default(client, pid, standard_choice("device"))
    _session(
        client,
        tid,
        "analyst",
        lease={
            "kind": "device",
            "device_id": lab,
            "status": "ready",
            "room_resource_id": tid,
        },
    )

    vis = _visibility(client, tid)
    assert _topic_binding(client, tid) is None
    assert vis["effective"] == "isolated"
    assert vis["machine_access"] is False


def test_a_room_moved_to_an_enrolled_machine_later_runs_isolated_there(client):
    pid = _project(client)
    tid = _topic(client, pid)
    (lab,) = _project_devices(client, pid, "lab")
    _project_default(client, pid, standard_choice("cloud"))
    _session(client, tid, "writer", choice=standard_choice("cloud"))
    _session(client, tid, "analyst", choice=standard_choice("cloud"))
    assert _visibility(client, tid)["machine_access"] is False

    from tests.integration.conftest import session_auth_headers

    moved = client.put(
        f"/topics/{tid}/compute-profile",
        headers=session_auth_headers("andyl"),
        json={"choice": {"name": "Lab", "profile": "device", "device_id": lab}},
    )
    assert moved.status_code == 200, moved.text

    vis = _visibility(client, tid)
    assert _topic_binding(client, tid).device_id == lab
    assert vis["effective"] == "isolated"
    assert vis["machine_access"] is False


def test_a_room_whose_next_session_starts_on_an_enrolled_machine_says_so(client):
    """Before anyone has run, the badge answers for where the first agent goes:
    isolated on an enrolled machine nobody gave it (#2320)."""
    pid = _project(client)
    tid = _topic(client, pid)
    (lab,) = _project_devices(client, pid, "lab")
    _project_default(
        client, pid, ComputeChoice(name="Lab", profile="device", device_id=lab)
    )

    vis = _visibility(client, tid)
    assert vis["effective"] == "isolated"
    assert vis["machine_access"] is False


def test_sessions_on_cloud_machines_show_no_badge(client):
    """A Cloud box is the room's own: seeing all of it grants nothing more,
    even when it reaches the room through the device transport."""
    pid = _project(client)
    tid = _topic(client, pid)

    async def _cloud_box() -> str:
        async with client.test_factory() as session:
            service = sql_device_service(session)
            code = await service.start("cloud-box")
            device = await service.approve(code, owner_user_id=1, supply=Supply.cloud)
            await session.commit()
            return device.device_id

    box = asyncio.run(_cloud_box())
    _project_default(client, pid, standard_choice("cloud"))
    _session(
        client,
        tid,
        "analyst",
        choice=standard_choice("cloud"),
        lease={"kind": "device", "device_id": box, "status": "ready"},
    )

    vis = _visibility(client, tid)
    assert vis["effective"] is None
    assert vis["machine_access"] is False


def test_a_denied_tier_is_refused_out_loud_even_when_the_room_is_running(client):
    """档位处置写成 `deny` 时，开跑的房间要的那一档拿到的是拒绝，不是提议（I27）。

    提议读起来是「再等等，有人会点头」；拒绝说的是「这条路不通，换一档」。房间开
    没开跑不改变这个答案 —— 「超档怎么办」只有 `policy/gate.py` 回答，路由不因为
    「这一轮还要人点头」就把那一问跳过去。
    """
    from tests.integration.conftest import session_auth_headers

    pid = _project(client)
    tid = _topic(client, pid)
    here, there = _project_devices(client, pid, "here", "there")
    assert (
        client.put(
            f"/topics/{tid}/compute-profile",
            json={"profile": "device", "device_id": here},
        ).status_code
        == 200
    )
    gated = client.put(
        f"/projects/{pid}/tier-policy",
        json={"allowed_tiers": ["included"], "over_tier": "deny"},
        headers=session_auth_headers("andyl"),
    )
    assert gated.status_code == 200, gated.text
    _mark_started(client, tid)
    seat = _agent_seat(client, tid)

    refused = client.put(
        f"/topics/{tid}/compute-profile",
        json={"profile": "device", "device_id": there},
        headers={
            "X-Cheese-Token": mint_scoped_token(
                project_id=pid, topic_id=tid, agent_handle=seat
            )
        },
    )

    assert refused.status_code == 422, refused.text
    assert "不在本项目允许的档位内" in refused.json()["message"], refused.text
    assert _topic_binding(client, tid).device_id == here
