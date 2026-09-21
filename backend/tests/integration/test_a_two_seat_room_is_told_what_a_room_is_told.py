"""私聊是名册两席的房间，所以它听到的和普通房间听到的是同一套（结论 19）。

判据②（ARCH §9.1「私聊」行）：``is_private`` 只剩「名册两席」和「草稿区」两类读
点。三处最容易各自再问一遍那个布尔的地方——实况文档、验收卡、阶段——在这里逐字对
过：同一份文档、同一张等采纳的卡，私聊和普通两席房间拿到的 system prompt 在这三处
一模一样。

它们从前各问一次 ``topic.is_private``：私聊拿不到实况文档、验收卡查都不查、阶段是
None。于是一间递了卡在等人采纳的私聊，提示词里一个字都不提这件事。
"""

import uuid

import pytest

from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.agent.skills import load_scenario
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.project.services import ProjectService
from app.domain.review.repositories import AcceptCardRepository
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService
from tests.conftest import StubChannel, settle_turn

pytestmark = pytest.mark.anyio

DOC = "## 实况\n这间房正在做的事：把两席收进名册。"


class Screen(StubChannel):
    async def send_prompt(self, screen: uuid.UUID, prompt: str) -> bool:
        self.starts(screen, session_id="two-seats")
        self.stops(screen, "Done.", session_id="two-seats")
        return True


async def _prompt_of(client, tmp_path, *, private: bool) -> tuple[str, int]:
    """跑一轮，交回这一轮的 system prompt 和这间房名册上的席位数。"""
    factory = client.test_factory
    screen = Screen()
    svc = ChatService(
        session_factory=factory,
        compute=ComputePool([screen.runtime], screen.name),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        if private:
            topic = await TopicService(session).get_or_create_private(
                project_id=project.id, user_handle="u"
            )
        else:
            topic = await TopicService(session).create(
                project_id=project.id, title="Work", created_by="u"
            )
        topic_id = topic.id
        await BlockRepository(session).add(
            project_id=project.id,
            topic_id=topic_id,
            author="u",
            author_type=AuthorType.participant,
            content=DOC,
            kind=BlockKind.doc,
        )
        # 一张等人采纳的卡：阶段由它推出来（stages.resolve_stage）。
        await AcceptCardRepository(session).add(
            topic_id=topic_id, reviewer_handle="u", routing_reason="最懂"
        )
        await session.commit()
    async with factory() as session:
        _, seats = await TopicMemberService(session).list_for_topic(topic_id)
    async for _ in svc.converse(
        topic_id=topic_id, author="u", content="说说进展", summon=True
    ):
        pass
    await settle_turn(svc, topic_id)
    assert screen.last_system_prompt is not None
    return screen.last_system_prompt, seats


async def test_a_dm_is_told_the_doc_the_card_and_the_stage(client, tmp_path):
    """合同：实况文档、验收卡、阶段，两席的私聊和两席的普通房间一模一样。"""
    private_prompt, private_seats = await _prompt_of(client, tmp_path, private=True)
    room_prompt, room_seats = await _prompt_of(client, tmp_path, private=False)

    # 前提：两边都是两席，比的才是「私聊 vs 普通」而不是「两席 vs 多席」。
    assert private_seats == 2
    assert room_seats == 2

    awaiting = load_scenario("stage:awaiting")
    assert awaiting, "stage:awaiting 这一段得有内容，否则下面的断言证明不了什么"
    for prompt in (private_prompt, room_prompt):
        # 实况文档
        assert "## 当前话题的实况文档" in prompt
        assert DOC in prompt
        # 验收卡 → 阶段：卡在等人采纳，提示词里给的就是那一段
        assert awaiting in prompt
