"""写记忆前查的那条检出目录，是**这位 agent 自己**的，且只认这一代（结论 60）。

手是 agent 的，不是房间的：一间房可以坐着不止一条会话，队友各有各的工作树。拿房
间里第一条带租约的地点去查，就可能在另一位队友的检出目录里命中——而命中就是拒掉
一次写入。按这个模块自己的口径，挡住一条本该记下的事实比多记一条重复的糟得多，所
以拿错目录比查不成更糟。

换代同理：房间重开会换 ``resource_id``，上一代残留的租约不是本次的手。
"""

import uuid

import pytest

from app.domain.agent import execution
from app.domain.agent.harness import harness_for
from app.domain.agent_session.services import AgentSessionService
from app.domain.memory.redundant import agent_checkout_search
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from tests.integration.conftest import registered

pytestmark = pytest.mark.anyio

HANDS_OF_THE_REVIEWER = "reviewer-hands"
HANDS_OF_THE_DEFAULT = "default-hands"


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


async def test_another_teammates_hands_are_not_this_agents_checkout(
    business_db_factory, monkeypatch
):
    """房间里坐着另一位队友那双手，问的人是 reviewer——那双手不是它的。

    拿房间里第一条带租约的地点，这里就会去默认那位的检出目录里查，命中就是一次凭空
    拒绝。reviewer 自己有手之后，查的才是它自己那条。
    """
    asked = _recording(monkeypatch)
    async with business_db_factory() as session:
        project, room = await _room(session)
        harness = harness_for(project.settings)
        sessions = AgentSessionService(session)
        generation = str(room.resource_id or room.id)
        await sessions.remember_place(
            topic_id=room.id,
            agent_handle="cheese",
            harness=harness,
            work_lease={"kind": "device", "device_id": HANDS_OF_THE_DEFAULT},
            runtime_location=_at(HANDS_OF_THE_DEFAULT, generation=generation),
        )
        await session.flush()

        search = agent_checkout_search(session, room, "reviewer", harness)
        assert await search(["pnpm"]) == []
        assert asked == []

        await sessions.remember_place(
            topic_id=room.id,
            agent_handle="reviewer",
            harness=harness,
            work_lease={"kind": "device", "device_id": HANDS_OF_THE_REVIEWER},
            runtime_location=_at(HANDS_OF_THE_REVIEWER, generation=generation),
        )
        await session.flush()

        assert await search(["pnpm"]) == []

    assert [t["device_id"] for t in asked] == [HANDS_OF_THE_REVIEWER]


async def test_a_lease_from_a_previous_generation_is_not_this_turns_hand(
    business_db_factory, monkeypatch
):
    asked = _recording(monkeypatch)
    async with business_db_factory() as session:
        project, room = await _room(session)
        harness = harness_for(project.settings)
        await AgentSessionService(session).remember_place(
            topic_id=room.id,
            agent_handle="reviewer",
            harness=harness,
            work_lease={"kind": "device", "device_id": HANDS_OF_THE_REVIEWER},
            runtime_location=_at(HANDS_OF_THE_REVIEWER, generation=str(uuid.uuid4())),
        )
        await session.flush()

        search = agent_checkout_search(session, room, "reviewer", harness)
        assert await search(["pnpm"]) == []

    # 查不成就存下来（返回空），但不许把上一代的残留当成本次的手去查。
    assert asked == []
