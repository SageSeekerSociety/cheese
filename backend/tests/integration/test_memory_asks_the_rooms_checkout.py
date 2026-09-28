"""写记忆前查的那条检出目录，是**这个房间**的，且只认这一代。

手是房间的（一个话题一个容器，2026-09-28 决定，推翻结论 60）：一间房只有一条算力选
择，房间里坐着的每一条会话都工作在那一台机器上，所以「这件事 repo 里写了没有」问的
是房间那一份工作树——问哪一位队友都一样。按会话去问只会多出一个「这位队友还没开
工」的空答案，而空答案在这里等于静默放行。

换代同理：房间重开会换 ``resource_id``，上一代残留的租约不是本次的手。
"""

import uuid

import pytest

from app.domain.agent import execution
from app.domain.agent_session.services import AgentSessionService
from app.domain.memory.redundant import room_checkout_search
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from tests.integration.conftest import registered

pytestmark = pytest.mark.anyio

HANDS_OF_THE_ROOM = "room-hands"
HANDS_OF_ANOTHER_ROOM = "another-rooms-hands"


def _at(device: str, *, generation: str) -> dict:
    return {
        "device_id": device,
        "channel": "device",
        "resource_id": generation,
        "runtime": {},
    }


async def _room(session):
    await registered(session, "andyl")
    project = await ProjectService(session).create(
        name="两位队友一间房", owner_handle="andyl", forge_kind="github_app"
    )
    room = await TopicService(session).create(
        project_id=project.id, title="干活", created_by="andyl"
    )
    return project, room


def _recording(monkeypatch) -> list[dict]:
    """把执行器调用记下来，还回「repo 里没有」——这条用例问的是找谁查。"""
    asked: list[dict] = []

    async def call(target, method, params):
        asked.append(target)
        return {"searched": True, "hits": []}

    monkeypatch.setattr(execution, "call", call)
    return asked


async def test_the_room_asks_its_own_checkout_whichever_teammate_writes(
    business_db_factory, monkeypatch
):
    """房间里坐着两位队友，谁写记忆都问房间那一份工作树。

    一个话题一个容器：两位队友的手在同一台机器上，所以「repo 里写了没有」在房间里
    只有一个答案——写的人是哪一位都一样，查的是同一棵检出。
    """
    asked = _recording(monkeypatch)
    async with business_db_factory() as session:
        _project, room = await _room(session)
        harness = "claude-code"
        sessions = AgentSessionService(session)
        generation = str(room.resource_id or room.id)
        await sessions.remember_place(
            topic_id=room.id,
            agent_handle="cheese",
            harness=harness,
            work_lease={"kind": "device", "device_id": HANDS_OF_THE_ROOM},
            runtime_location=_at(HANDS_OF_THE_ROOM, generation=generation),
        )
        await session.flush()

        # 另一位队友还没租到手，可房间的检出只有一个。
        assert await room_checkout_search(session, room)(["pnpm"]) == []
        assert await room_checkout_search(session, room)(["pnpm"]) == []

    assert [t["device_id"] for t in asked] == [HANDS_OF_THE_ROOM, HANDS_OF_THE_ROOM]


async def test_a_lease_from_a_previous_generation_is_not_this_turns_hand(
    business_db_factory, monkeypatch
):
    asked = _recording(monkeypatch)
    async with business_db_factory() as session:
        _project, room = await _room(session)
        await AgentSessionService(session).remember_place(
            topic_id=room.id,
            agent_handle="reviewer",
            harness="claude-code",
            work_lease={"kind": "device", "device_id": HANDS_OF_ANOTHER_ROOM},
            runtime_location=_at(HANDS_OF_ANOTHER_ROOM, generation=str(uuid.uuid4())),
        )
        await session.flush()

        assert await room_checkout_search(session, room)(["pnpm"]) == []

    # 查不成就存下来（返回空），但不许把上一代的残留当成本次的手去查。
    assert asked == []
