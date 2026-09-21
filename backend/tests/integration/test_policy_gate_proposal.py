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
from tests.conftest import seed_user
from tests.integration.conftest import chat_ws_url, session_auth_headers


def _user_id(client, handle: str) -> int:
    """一个真人，外加他的 id —— 机主和收件箱都是按 id 记的。

    建人走共享的那份 get-or-create（`tests/conftest.py` 的 `seed_user`，integration
    这一层用的就是它），这里只是把刚建好的那个按 handle 取回来。
    """
    seed_user(client, handle)

    async def _read() -> int:
        async with client.test_factory() as session:
            user = await UserRepository(session).get_by_username(handle)
            assert user is not None
            return user.id

    return asyncio.run(_read())


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
    _user_id(client, "andyl")
    owner_id = _user_id(client, "xiaowang")
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
    owner_id = _user_id(client, "xiaowang")
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
    _user_id(client, "andyl")
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
    _user_id(client, "andyl")
    pid = _project(client)
    response = client.put(
        f"/projects/{pid}/tier-policy",
        json={"over_tier": "downgrade"},
        headers=session_auth_headers("andyl"),
    )
    assert response.status_code == 422


# --- 轮次这一侧：决定一个房间占谁的机器、用哪个模型的那段代码 ------------------
#
# 上面几条走的是「人主动去点算力选择器」。下面这几条走的是默认路径：没人碰过选择
# 器的房间，第一轮由轮次组装自己解析出机器和模型。闸门必须也在那里，否则被闸住的
# 只剩少数手动情形，而项目设了策略之后随便 @ 一句芝士照样把机器占掉。


def _say(client, topic_id: str, text: str = "帮我看看", who: str = "andyl") -> list:
    """在房间里说一句并 @ 芝士，收完这一轮的帧。

    @ 写在正文里：「这一句点了谁的名」是服务端从正文解析出来的（I13），帧上没有
    那一位，所以不 @ 就没有人被叫起来，闸门也就轮不到撞。
    """
    with client.websocket_connect(chat_ws_url(topic_id, who)) as ws:
        ws.send_json({"type": "message", "content": f"@芝士 {text}"})
        frames = []
        while True:
            frames.append(ws.receive_json())
            if frames[-1]["type"] in ("done", "error"):
                break
    return frames


def _errors(frames: list) -> list:
    """这一轮是不是以一次报错收场。

    propose 和 deny 在轮次这条路上的差别就在这里：`_say` 的循环在 `done` 和
    `error` 两种帧上都会停，所以只断言「房间里有一条提议」的用例在两种处置下都是
    绿的。判据②（产物是一条提议）要断言的是**没有** error 帧，判据③（一次看得见
    的拒绝）要断言的是有。
    """
    return [f for f in frames if f["type"] == "error"]


def _proposals(client, topic_id: str) -> list[Block]:
    return [
        block
        for block in _room_events(client, topic_id)
        if (block.meta or {}).get("event_type") == EVENT_POLICY_PROPOSAL
    ]


def _gated(client, *, allowed: list[str], over_tier: str) -> tuple[str, str, int]:
    owner_id = _user_id(client, "andyl")
    pid = _project(client)
    tid = _topic(client, pid)
    saved = client.put(
        f"/projects/{pid}/tier-policy",
        json={"allowed_tiers": allowed, "over_tier": over_tier},
        headers=session_auth_headers("andyl"),
    )
    assert saved.status_code == 200, saved.text
    return pid, tid, owner_id


def test_a_room_nobody_configured_still_passes_the_gate(client, stub_hooks):
    """没人打开过算力选择器的房间：占机器的那一刻照样撞闸门。

    `included` 之外一档都不许，而机器无论解析成自有设备（byo）还是 Cloud
    （premium）都在档外 —— 于是第一轮把机器占下来之前就变成了一条提议。
    """
    _pid, tid, owner_id = _gated(client, allowed=["included"], over_tier="propose")

    frames = _say(client, tid)

    proposals = _proposals(client, tid)
    assert len(proposals) == 1
    assert "算力" in proposals[0].content
    # 产物是一条提议，不是一次报错：这一轮以房间里那条提议加一个 done 收场，发消
    # 息的人在流里读到的就是它。
    assert _errors(frames) == []
    assert frames[-1]["type"] == "done"
    assert [
        f
        for f in frames
        if f["type"] == "event_block" and "算力" in f["block"]["content"]
    ]
    # 这一轮没有发生：没有请求发出去，机器也没有被绑走。
    assert stub_hooks.last_prompt is None
    assert _topic_binding(client, tid) is None
    # 这个项目一台机器也没接，平台挑不出那一台，所以点头的是项目的主人。有机器的
    # 情形在下面那条里——点头的是**那台机器的主人**，哪怕没人碰过选择器。
    assert _inbox(client, owner_id) != []


def test_the_owner_of_the_machine_the_platform_would_pick_is_the_one_asked(
    client, stub_hooks, monkeypatch
):
    """结论 40「要那台机器的主人点头」在默认路径上也成立。

    没人打开过算力选择器的房间——绝大多数房间——平台在第一轮自己挑一台在线的项目
    机器，而那台机器完全可能是别的成员的。被问的必须是他，不是项目的主人：花的是
    他的电和带宽，也只有他点得了这个头。
    """
    monkeypatch.setattr(device_hub, "is_online", lambda _device_id: True)
    project_owner_id = _user_id(client, "andyl")
    machine_owner_id = _user_id(client, "xiaowang")
    pid = _project(client)
    tid = _topic(client, pid)
    _device_owned_by(client, pid, machine_owner_id, "小王的工作站")
    # 项目默认是「自有设备 · 自动选择」：没点名任何一台，平台第一轮自己挑。
    saved = client.put(
        f"/projects/{pid}/compute-configs",
        json={
            "default": {"name": "自有设备 · 自动选择", "profile": "device"},
            "favorites": [],
        },
        headers=session_auth_headers("andyl"),
    )
    assert saved.status_code == 200, saved.text
    gated = client.put(
        f"/projects/{pid}/tier-policy",
        json={"allowed_tiers": ["included"], "over_tier": "propose"},
        headers=session_auth_headers("andyl"),
    )
    assert gated.status_code == 200, gated.text

    frames = _say(client, tid)

    proposals = _proposals(client, tid)
    assert len(proposals) == 1
    assert "小王的工作站" in proposals[0].content
    assert "xiaowang" in proposals[0].content
    assert _errors(frames) == []
    assert _inbox(client, machine_owner_id) != []
    assert _inbox(client, project_owner_id) == []
    # 这次调用没有发生：机器没被绑走，也没有请求发出去。
    assert _topic_binding(client, tid) is None
    assert stub_hooks.last_prompt is None


def test_the_picker_and_the_turn_ask_for_the_same_machine_once(
    client, stub_hooks, monkeypatch
):
    """两条路走到同一台机器，是一条提议，不是两条。

    在算力选择器里点「小王的工作站」撞上策略 → 一条提议；绑定因此没写，于是同一个
    人回房间 @ 一句芝士，轮次组装自己解析，解析出来的还是那台机器。两处说的是同一
    个诉求（同一个房间、同一台机器、同一档），所以房间里只该有一条「等谁点头」、
    小王只该收到一条通知（结论 15 / I11「一次」）。
    """
    monkeypatch.setattr(device_hub, "is_online", lambda _device_id: True)
    _user_id(client, "andyl")
    machine_owner_id = _user_id(client, "xiaowang")
    pid = _project(client)
    tid = _topic(client, pid)
    device_id = _device_owned_by(client, pid, machine_owner_id, "小王的工作站")
    saved = client.put(
        f"/projects/{pid}/compute-configs",
        json={
            "default": {"name": "自有设备 · 自动选择", "profile": "device"},
            "favorites": [],
        },
        headers=session_auth_headers("andyl"),
    )
    assert saved.status_code == 200, saved.text
    gated = client.put(
        f"/projects/{pid}/tier-policy",
        json={"allowed_tiers": ["included"], "over_tier": "propose"},
        headers=session_auth_headers("andyl"),
    )
    assert gated.status_code == 200, gated.text

    picked = client.put(
        f"/topics/{tid}/compute-profile",
        json={"profile": "device", "device_id": device_id},
        headers=session_auth_headers("andyl"),
    )
    assert picked.status_code == 200, picked.text
    assert picked.json()["data"]["proposal"]["approver"] == "xiaowang"

    frames = _say(client, tid)

    assert len(_proposals(client, tid)) == 1
    assert len(_inbox(client, machine_owner_id)) == 1
    # 第二次问同一件事也不是报错：房间里不再多一条，这一轮照样以 done 收场。
    assert _errors(frames) == []
    assert frames[-1]["type"] == "done"
    assert stub_hooks.last_prompt is None


def test_a_turn_whose_model_is_over_tier_sends_no_request(client, stub_hooks):
    """判据②：超档且处置为变提议时，产物是一条提议，一个请求也没发出去。

    机器那两档都放行，撞闸门的只剩模型 —— 项目默认模型是 `included` 档，而这里恰
    好不允许这一档自己发生。
    """
    _pid, tid, owner_id = _gated(
        client, allowed=["byo", "premium", "frontier"], over_tier="propose"
    )

    frames = _say(client, tid)

    proposals = _proposals(client, tid)
    assert len(proposals) == 1
    assert "模型" in proposals[0].content
    # 判据②与判据③的分界：产物是提议的那一轮里，一个 error 帧也没有。
    assert _errors(frames) == []
    assert stub_hooks.last_prompt is None
    assert _inbox(client, owner_id) != []


def test_a_refused_model_is_not_quietly_swapped_for_a_cheaper_one(client, stub_hooks):
    """判据③ / I27：处置为拒绝时是一次看得见的拒绝，不是悄悄降档。"""
    _pid, tid, _owner_id = _gated(
        client, allowed=["byo", "premium", "frontier"], over_tier="deny"
    )

    frames = _say(client, tid)

    # 说得出口：这句话到了发消息的人眼前。
    refusals = _errors(frames)
    assert refusals and "档" in refusals[0]["message"]
    # 而且真的没跑：没有换一个档内的模型接着跑完这一轮。
    assert stub_hooks.last_prompt is None
    # 拒绝不是提议，房间里不该多出一条等人点头的事件。
    assert _proposals(client, tid) == []


def test_the_same_proposal_is_made_once_no_matter_how_often_it_is_asked(
    client, stub_hooks
):
    """结论 15 / I11「一次」：同一条提议不随每一轮重发。

    撞上策略的调用会反复发生——房间里每来一条消息就解析一次。人要收到的只有一条。
    """
    _pid, tid, owner_id = _gated(client, allowed=["included"], over_tier="propose")

    frames = [
        _say(client, tid, "第一句"),
        _say(client, tid, "第二句"),
        _say(client, tid, "第三句"),
    ]

    assert len(_proposals(client, tid)) == 1
    assert [_errors(turn) for turn in frames] == [[], [], []]
    assert len(_inbox(client, owner_id)) == 1
    assert stub_hooks.last_prompt is None
