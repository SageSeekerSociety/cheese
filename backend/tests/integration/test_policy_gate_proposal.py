"""要一台自托管机器撞上档位策略：机主读到的是一条提议，机器没有被占用。

闸门本身的四条判据在 `tests/unit/test_policy_gate.py`（纯的）。这里走的是提议**真
的到人手上**那一段：房间里落下一条事件，寻址点到机主，账本把它投进机主的收件箱
（结论 40 后半 = P24 的寻址 + P25 的投递，不是第三条通道）。

顺带守住「这次调用没有发生」：绑定不写，房间的算力选择不动——提议不是一次先斩后
奏的执行加一句通知。
"""

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.domain.agent.device_hub import device_hub
from app.domain.agent.platform_notices import EVENT_POLICY_PROPOSAL
from app.domain.block.models import Block, BlockKind
from app.domain.device.supply import Supply, Visibility
from app.domain.device.wiring import sql_device_service
from app.domain.notification.models import Notification
from app.domain.user.repositories import UserRepository
from tests.integration.conftest import session_auth_headers


def _seed_user(client, handle: str) -> int:
    async def _run() -> int:
        async with client.test_factory() as session:
            repo = UserRepository(session)
            user = await repo.get_by_username(handle)
            if user is None:
                user = await repo.create_user(
                    username=handle, email=f"{handle}@example.com"
                )
            await session.commit()
            return user.id

    return asyncio.run(_run())


def _project(client, owner: str = "andyl") -> str:
    return client.post("/projects", json={"name": "P", "owner_handle": owner}).json()[
        "data"
    ]["id"]


def _topic(client, pid: str) -> str:
    return client.post(
        "/topics", json={"project_id": pid, "title": "T", "created_by": "andyl"}
    ).json()["data"]["id"]


def _device_owned_by(client, pid: str, owner_user_id: int, name: str) -> str:
    async def _seed() -> str:
        async with client.test_factory() as session:
            service = sql_device_service(session)
            code = await service.start(name)
            device = await service.approve(
                code,
                owner_user_id=owner_user_id,
                supply=Supply.self_hosted,
                visibility=Visibility.isolated,
            )
            await service.assign_to_project(
                device.device_id, uuid.UUID(pid), actor_user_id=owner_user_id
            )
            await session.commit()
            return device.device_id

    return asyncio.run(_seed())


def _topic_binding(client, tid: str):
    async def _read():
        async with client.test_factory() as session:
            return await sql_device_service(session).topic_binding(uuid.UUID(tid))

    return asyncio.run(_read())


def _room_events(client, tid: str) -> list[Block]:
    async def _read() -> list[Block]:
        async with client.test_factory() as session:
            rows = await session.scalars(
                select(Block).where(
                    Block.topic_id == uuid.UUID(tid), Block.kind == BlockKind.event
                )
            )
            return list(rows)

    return asyncio.run(_read())


def _inbox(client, user_id: int) -> list[Notification]:
    async def _read() -> list[Notification]:
        async with client.test_factory() as session:
            rows = await session.scalars(
                select(Notification).where(Notification.receiver_id == user_id)
            )
            return list(rows)

    return asyncio.run(_read())


@pytest.fixture
def gated_project(client, monkeypatch):
    """一个只允许 `included` 档自己发生、超档变提议的项目，外加一台别人的机器。"""
    monkeypatch.setattr(device_hub, "is_online", lambda _device_id: True)
    _seed_user(client, "andyl")
    owner_id = _seed_user(client, "xiaowang")
    pid = _project(client)
    tid = _topic(client, pid)
    device_id = _device_owned_by(client, pid, owner_id, "小王的工作站")
    saved = client.put(
        f"/projects/{pid}/tier-policy",
        json={"allowed_tiers": ["included"], "over_tier": "propose"},
        headers=session_auth_headers("andyl"),
    )
    assert saved.status_code == 200, saved.text
    return pid, tid, device_id, owner_id


def test_asking_for_a_self_hosted_machine_reaches_its_owner_as_a_proposal(
    client, gated_project
):
    pid, tid, device_id, owner_id = gated_project

    response = client.put(
        f"/topics/{tid}/compute-profile",
        json={"profile": "device", "device_id": device_id},
        headers=session_auth_headers("andyl"),
    )

    assert response.status_code == 200, response.text
    proposal = response.json()["data"]["proposal"]
    assert proposal is not None
    assert proposal["approver"] == "xiaowang"

    # 房间里留下的是一条事件，写着这一步等谁。
    events = _room_events(client, tid)
    proposals = [
        block
        for block in events
        if (block.meta or {}).get("event_type") == EVENT_POLICY_PROPOSAL
    ]
    assert len(proposals) == 1
    assert "小王的工作站" in proposals[0].content
    assert "xiaowang" in proposals[0].content

    # 机主收到了它——走的是平台唯一那条投递路径，不是给提议新开的通道。
    assert [n for n in _inbox(client, owner_id)] != []


def test_a_proposal_does_not_take_the_machine(client, gated_project):
    """提议不是先斩后奏：绑定没写，房间的算力选择也没动。"""
    pid, tid, device_id, _owner_id = gated_project
    before = client.get(f"/topics/{tid}/compute-profile").json()["data"]["current"]

    response = client.put(
        f"/topics/{tid}/compute-profile",
        json={"profile": "device", "device_id": device_id},
        headers=session_auth_headers("andyl"),
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["proposal"] is not None
    assert _topic_binding(client, tid) is None
    after = client.get(f"/topics/{tid}/compute-profile").json()["data"]
    assert after["current"] == before
    assert after["inherited"] is True


def test_an_unrestricted_project_still_takes_the_machine(client, monkeypatch):
    """默认不限档 —— 今天的行为一点没变，闸门对它是透明的。"""
    monkeypatch.setattr(device_hub, "is_online", lambda _device_id: True)
    owner_id = _seed_user(client, "xiaowang")
    pid = _project(client)
    tid = _topic(client, pid)
    device_id = _device_owned_by(client, pid, owner_id, "小王的工作站")

    response = client.put(
        f"/topics/{tid}/compute-profile",
        json={"profile": "device", "device_id": device_id},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["proposal"] is None
    assert _topic_binding(client, tid).device_id == device_id


def test_the_policy_a_project_saved_is_the_policy_it_reads_back(client):
    _seed_user(client, "andyl")
    pid = _project(client)
    default = client.get(
        f"/projects/{pid}/tier-policy", headers=session_auth_headers("andyl")
    ).json()["data"]
    # 没说过 = 不限档 + 超档拒绝，也就是今天的行为。
    assert default["allowed_tiers"] is None
    assert default["over_tier"] == "deny"
    # 档位不是手写的白名单：目录里现在有哪几档，这里就报哪几档。
    assert "included" in default["tiers"]

    saved = client.put(
        f"/projects/{pid}/tier-policy",
        json={"allowed_tiers": ["included"], "over_tier": "propose"},
        headers=session_auth_headers("andyl"),
    ).json()["data"]
    assert saved["allowed_tiers"] == ["included"]
    assert saved["over_tier"] == "propose"

    cleared = client.put(
        f"/projects/{pid}/tier-policy",
        json={"allowed_tiers": None},
        headers=session_auth_headers("andyl"),
    ).json()["data"]
    assert cleared["allowed_tiers"] is None


def test_an_unknown_disposition_is_refused(client):
    _seed_user(client, "andyl")
    pid = _project(client)
    response = client.put(
        f"/projects/{pid}/tier-policy",
        json={"over_tier": "downgrade"},
        headers=session_auth_headers("andyl"),
    )
    assert response.status_code == 422
