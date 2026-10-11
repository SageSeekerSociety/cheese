"""一轮要用的模型撞上档位策略：产物是一条到人手上的提议，请求没有发出去。

闸门本身的判据在 `tests/unit/test_policy_gate.py`（纯的）。这里走的是提议**真的到
人手上**那一段：房间里落下一条事件，寻址点到该点头的人，账本把它投进收件箱。
"""

import asyncio
import uuid

from sqlalchemy import select

from app.domain.agent.platform_notices import EVENT_POLICY_PROPOSAL
from app.domain.block.models import Block, BlockKind
from app.domain.notification.models import Notification
from app.domain.user.repositories import UserRepository
from tests.conftest import seed_user
from tests.integration.conftest import (
    in_thread,
    post_message,
    post_project,
    room_socket,
    session_auth_headers,
)


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
    return post_project(client, json={"name": "P"}, owner=owner).json()["data"]["id"]


def _topic(client, pid: str) -> str:
    return client.post(
        "/topics",
        json={"project_id": pid, "title": "T"},
        headers=session_auth_headers("andyl"),
    ).json()["data"]["id"]


def _room_events(client, tid: str) -> list[Block]:
    async def _read() -> list[Block]:
        async with client.test_factory() as session:
            rows = await session.scalars(
                select(Block).where(
                    Block.conversation_id == uuid.UUID(tid),
                    Block.kind == BlockKind.event,
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


# --- 轮次这一侧：决定一轮用哪个模型的那段代码 ------------------------------


def _say(client, topic_id: str, text: str = "帮我看看", who: str = "andyl") -> list:
    """在房间里说一句并 @ 芝士，收完这一轮的帧。

    @ 写在正文里：「这一句点了谁的名」是服务端从正文解析出来的（I13），帧上没有
    那一位，所以不 @ 就没有人被叫起来，闸门也就轮不到撞。
    """
    # 芝士 answers in a 支线 of the room; the room's machine is what it takes.
    thread = in_thread(client, topic_id, who)
    with room_socket(client, thread, who) as ws:
        post_message(client, thread, who, {"content": f"@芝士 {text}"})
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


def test_a_turn_whose_model_is_over_tier_sends_no_request(client, stub_hooks):
    """判据②：超档且处置为变提议时，产物是一条提议，一个请求也没发出去。

    项目默认模型是 `included` 档，而这里恰好不允许这一档自己发生。
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
    项目默认模型在 `included` 档，这里不许它自己发生。
    """
    _pid, tid, owner_id = _gated(
        client, allowed=["byo", "premium", "frontier"], over_tier="propose"
    )

    frames = [
        _say(client, tid, "第一句"),
        _say(client, tid, "第二句"),
        _say(client, tid, "第三句"),
    ]

    assert len(_proposals(client, tid)) == 1
    assert [_errors(turn) for turn in frames] == [[], [], []]
    assert len(_inbox(client, owner_id)) == 1
    assert stub_hooks.last_prompt is None
