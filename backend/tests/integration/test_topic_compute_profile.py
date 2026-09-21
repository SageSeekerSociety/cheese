"""Topic compute-profile API (execution-architecture v4 会话级选择).

A topic picks its compute pool before its first turn; the choice sticks as the
project default and freezes once the topic has run (a session exists).
"""

import asyncio
import uuid
from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.core.sandbox_auth import mint_project_agent_credential, mint_scoped_token
from app.domain.agent.device_hub import device_hub
from app.domain.agent.device_provider import resolve_pinned_device
from app.domain.agent.harness.channel import ScreenSetupError
from app.domain.agent.platform_failures import DEVICE_OFFLINE_MESSAGE
from app.domain.agent_session.repositories import AgentSessionRepository
from app.domain.device.supply import Supply, Visibility
from app.domain.device.wiring import sql_device_service
from app.domain.identity.handles import CHEESE_HANDLE
from app.domain.machine.services import MachineService


def _project(client, owner: str = "andyl") -> str:
    return client.post("/projects", json={"name": "P", "owner_handle": owner}).json()[
        "data"
    ]["id"]


def _topic(client, pid: str) -> str:
    return client.post(
        "/topics", json={"project_id": pid, "title": "T", "created_by": "andyl"}
    ).json()["data"]["id"]


def _mark_started(client, tid: str) -> None:
    """Simulate the topic having run one turn (a session captured)."""

    async def _run() -> None:
        async with client.test_factory() as s:
            await AgentSessionRepository(s).save(
                topic_id=uuid.UUID(tid),
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
                    visibility=Visibility.isolated,
                )
                await service.assign_to_project(
                    device.device_id, uuid.UUID(pid), actor_user_id=1
                )
                device_ids.append(device.device_id)
            await session.commit()
            return device_ids

    return asyncio.run(_seed())


def _project_credential(client, pid: str) -> str:
    """这个项目自己的那张 agent 凭据 —— 到项目里哪个房间都认得。

    签发本身不给角色，所以先把那位芝士加进项目成员，凭据才够得着房间：这条用例要
    证的是「够得着也动不了」，不是「够不着」。
    """
    from tests.integration.conftest import session_auth_headers

    rows = client.get(f"/projects/{pid}/agents").json()["data"]["data"]
    (default,) = [row for row in rows if row["is_default"]]
    joined = client.post(
        f"/projects/{pid}/members",
        json={"user_handle": default["seat_handle"]},
        headers=session_auth_headers("andyl"),
    )
    assert joined.status_code == 200, joined.text
    return mint_project_agent_credential(project_id=pid, epoch=0)


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


def test_new_topic_inherits_default_and_is_unlocked(client):
    pid = _project(client)
    tid = _topic(client, pid)
    from app.core.config import settings
    from app.domain.agent.market import compute_default_name

    body = client.get(f"/topics/{tid}/compute-profile").json()["data"]
    # Nothing selected anywhere, so the deployment's own fallback applies — never
    # the retired local pool (#358). Last selection would win if there were one.
    assert body["current"] == compute_default_name(settings) == "device"
    assert body["locked"] is False
    assert body["inherited"] is True
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
    monkeypatch.setattr("app.api.routes.topics.project_device_online", _online)
    monkeypatch.setattr("app.api.routes.projects.project_device_online", _online)

    # An undeployed pool can't be selected.
    bad = client.put(f"/topics/{tid}/compute-profile", json={"profile": "gpu"})
    assert bad.status_code == 422
    # A retired one cannot either — no silent fallback to it.
    retired = client.put(
        f"/topics/{tid}/compute-profile", json={"profile": "local-docker"}
    )
    assert retired.status_code == 422

    r = client.put(f"/topics/{tid}/compute-profile", json={"profile": "device"})
    assert r.status_code == 200
    assert r.json()["data"]["current"] == "device"
    assert r.json()["data"]["inherited"] is False

    # Persisted on the topic (no longer inheriting)...
    tbody = client.get(f"/topics/{tid}/compute-profile").json()["data"]
    assert tbody["inherited"] is False
    # The deployment's existing device default is unchanged.
    pbody = client.get(f"/projects/{pid}/compute-profiles").json()["data"]
    assert pbody["current"] == "device"


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
    assert _resolve_topic_device(client, pid, tid, lambda _device_id: True) == named
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
    assert binding.visibility is Visibility.host
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
    assert body["devices"] == [
        {"device_id": office, "name": "办公室 Mac mini", "online": True},
        {"device_id": home, "name": "家里那台", "online": False},
    ]
    assert outside not in {device["device_id"] for device in body["devices"]}


def test_unlocked_topic_can_change_machine_but_locked_topic_cannot(client):
    pid = _project(client)
    tid = _topic(client, pid)
    first, second = _project_devices(client, pid, "first", "second")

    first_response = client.put(
        f"/topics/{tid}/compute-profile",
        json={"profile": "device", "device_id": first},
    )
    assert first_response.status_code == 200
    second_response = client.put(
        f"/topics/{tid}/compute-profile",
        json={"profile": "device", "device_id": second},
    )
    assert second_response.status_code == 200
    assert _topic_binding(client, tid).device_id == second

    _mark_started(client, tid)
    locked_response = client.put(
        f"/topics/{tid}/compute-profile",
        json={"profile": "device", "device_id": first},
    )
    assert locked_response.status_code == 422
    assert _topic_binding(client, tid).device_id == second


def test_the_turn_running_in_the_room_asks_and_does_not_switch(client):
    """`cheese_machine` 打的是这条真路由，而它换不动机器（结论 23、40）。

    这个工具只会被**正在这个房间里跑的那一轮**调用，而那一刻房间必然已经开跑过。
    所以「开跑即锁定」不能把它一起锁死：锁住它，表上就摆了一样在生产里一次也调不通
    的工具，它拿到的回话还是「新建话题可另选算力」——而它连新建话题都做不到。

    放它说得出口，不等于放它当场换：换过去丢掉的是这台机器上的工作区和还没提交的
    改动，所以产物是一条给机主的提议，钉一动不动。

    契约那一组对着一台假 HTTP 断言这次调用落在哪个地址上，答不出这里的问题：地址
    是对的，答话是拒绝。
    """
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
    _mark_started(client, tid)
    seat = _agent_seat(client, tid)

    # 界面上那个人：房间开跑了，这一档就定住了，连提议都没有。
    by_a_person = client.put(
        f"/topics/{tid}/compute-profile",
        json={"profile": "device", "device_id": there},
    )
    assert by_a_person.status_code == 422
    assert _topic_binding(client, tid).device_id == here

    # 房间里跑着的那一轮自己要另一台：说得出口，换不成，产物是一条给机主的提议。
    by_the_turn = client.put(
        f"/topics/{tid}/compute-profile",
        json={"profile": "device", "device_id": there},
        headers={
            "X-Cheese-Token": mint_scoped_token(
                project_id=pid, topic_id=tid, agent_handle=seat
            )
        },
    )

    assert by_the_turn.status_code == 200, by_the_turn.text
    body = by_the_turn.json()["data"]
    assert body["proposal"] is not None, body
    assert "there" in body["proposal"]["content"], body["proposal"]
    assert _topic_binding(client, tid).device_id == here, "钉被搬走了"
    assert body["device_id"] == here


def test_a_project_credential_cannot_move_a_room_that_is_already_running(client):
    """项目级 agent 凭据不是「这个房间这一轮」（结论 23）。

    它够得着这个项目里的每一个房间，所以拿「说话的是个 agent」当判据，等于任何一张
    项目凭据都能动别人正跑着的房间 —— 连一条提议都不该从它这里长出来。
    """
    pid = _project(client)
    tid = _topic(client, pid)
    here, there = _project_devices(client, pid, "here", "there")
    client.put(
        f"/topics/{tid}/compute-profile",
        json={"profile": "device", "device_id": here},
    )
    _mark_started(client, tid)

    refused = client.put(
        f"/topics/{tid}/compute-profile",
        json={"profile": "device", "device_id": there},
        headers={"X-Cheese-Token": _project_credential(client, pid)},
    )

    assert refused.status_code == 422, refused.text
    assert _topic_binding(client, tid).device_id == here


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


def test_visibility_block_is_present_and_carries_the_notice(client):
    """#282 §四 / #358: the compute-profile response a room reads carries the
    visibility 档 so the room can SHOW whether a turn sees the whole machine.

    The default is whichever 档 has a transport, and today that is whole-machine:
    boxed `isolated` is honestly undeployed until #358 step 2, so naming it the
    default here — as this test used to — told a room its topic was boxed while
    the resolver bound it to the whole machine. A topic with no pinned device is
    not a Hosted Machine turn at all, so `machine_access` is False."""
    pid = _project(client)
    tid = _topic(client, pid)
    vis = client.get(f"/topics/{tid}/compute-profile").json()["data"]["visibility"]

    opts = {o["id"]: o for o in vis["options"]}
    assert opts["isolated"]["available"] is False
    assert opts["isolated"]["default"] is False
    assert opts["host"]["available"] is True
    assert opts["host"]["default"] is True
    assert "整台机器" in opts["host"]["description"]
    # Survives step 2 flipping the answer: one default, and it can run.
    defaults = [o for o in vis["options"] if o["default"]]
    assert len(defaults) == 1 and defaults[0]["available"] is True

    assert vis["effective"] is None  # not pinned to any device
    assert vis["machine_access"] is False
    assert "整台机器" in vis["notice"]  # badge / tooltip copy is present


def test_two_topics_on_one_machine_report_their_own_visibility(client):
    """Visibility belongs to each topic↔machine binding, not to the device."""
    pid = _project(client)
    host_tid = _topic(client, pid)
    isolated_tid = _topic(client, pid)

    async def _pin_both_topics() -> None:
        from app.domain.device.service import DeviceService
        from app.domain.device.sql_repository import SqlDeviceRepository
        from app.domain.device.supply import Supply, Visibility

        async with client.test_factory() as session:
            svc = DeviceService(SqlDeviceRepository(session))
            code = await svc.start("dev-box")
            device = await svc.approve(
                code,
                owner_user_id=1,
                supply=Supply.self_hosted,
                visibility=Visibility.isolated,
            )
            await svc.bind_topic_device(
                uuid.UUID(host_tid), device.device_id, Visibility.host
            )
            await svc.bind_topic_device(
                uuid.UUID(isolated_tid), device.device_id, Visibility.isolated
            )
            await session.commit()

    asyncio.run(_pin_both_topics())
    host_visibility = client.get(f"/topics/{host_tid}/compute-profile").json()["data"][
        "visibility"
    ]
    isolated_visibility = client.get(f"/topics/{isolated_tid}/compute-profile").json()[
        "data"
    ]["visibility"]
    assert host_visibility["effective"] == "host"
    assert host_visibility["machine_access"] is True
    assert isolated_visibility["effective"] == "isolated"
    assert isolated_visibility["machine_access"] is False


def test_locked_once_topic_has_run(client):
    pid = _project(client)
    tid = _topic(client, pid)
    _mark_started(client, tid)

    body = client.get(f"/topics/{tid}/compute-profile").json()["data"]
    assert body["locked"] is True

    # Switching after the first turn is rejected — the pin is frozen.
    r = client.put(f"/topics/{tid}/compute-profile", json={"profile": "local-docker"})
    assert r.status_code == 422


def test_the_turn_can_ask_for_cloud_and_gets_a_proposal_not_a_401(client, monkeypatch):
    """`cheese_machine(profile="cloud")` 说得出口 —— 收件人是项目的主人（结论 23）。

    Cloud 花的是项目的钱，所以「谁有权花」这一问要的是一个登录用户；而房间里跑着
    的那一轮拿的每一张凭据都不是登录用户的（`api/auth.py` 给它们的 `via` 是
    `cheese`）。这一问要是排在闸门前面，这条路上的 Cloud 一档百分之百是 401，那条
    「等项目主人点头」的提议一次也长不出来 —— 而它正是这一档该有的产物。

    变成提议的那一次没有花任何人的钱：该点头的人就是项目的主人本人，他点头才是这
    笔钱的授权。所以那一问排在闸门之后 —— 这次调用真的要发生时才问。
    """
    pid = _project(client)
    tid = _topic(client, pid)
    monkeypatch.setattr(settings, "microcloud_base_url", "https://cloud.example")
    monkeypatch.setattr(settings, "microcloud_tenant_secret", "secret")
    provision = AsyncMock()
    monkeypatch.setattr(MachineService, "provision", provision)
    _mark_started(client, tid)
    seat = _agent_seat(client, tid)

    asked = client.put(
        f"/topics/{tid}/compute-profile",
        json={"profile": "cloud"},
        headers={
            "X-Cheese-Token": mint_scoped_token(
                project_id=pid, topic_id=tid, agent_handle=seat
            )
        },
    )

    assert asked.status_code == 200, asked.text
    body = asked.json()["data"]
    assert body["proposal"] is not None, body
    assert body["proposal"]["approver"] == "andyl", body["proposal"]
    # 这次调用没有发生：没有开机器，房间的算力也没被改写。
    provision.assert_not_awaited()
    assert body["current"] != "cloud", body


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
