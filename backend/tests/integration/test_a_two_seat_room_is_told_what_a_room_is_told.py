"""私聊是名册两席的房间，所以它听到的和普通房间听到的是同一套（结论 19）。

判据②（ARCH §9.1「私聊」行）：``is_private`` 只剩「名册两席」和「草稿区」两类读
点。每段对话都读的项目总览最容易被各自再问一遍那个布尔：实况文档从前问过
``topic.is_private``，私聊于是拿不到它。这里逐字对过：同一份总览，私聊和普通两席房间
开场时听到的一模一样。
"""

import uuid

import pytest

from app.api import deps as session_turn_deps
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService
from tests.conftest import StubChannel, settle_turn
from tests.integration.conftest import registered
from tests.support.living_doc import write_overview

pytestmark = pytest.mark.anyio

DOC = "## 实况\n这个项目要做的事：把两席收进名册。"


class Screen(StubChannel):
    async def send_prompt(self, screen: uuid.UUID, prompt: str) -> bool:
        self.starts(screen, session_id="two-seats")
        self.stops(screen, "Done.", session_id="two-seats")
        return True


async def _prompt_of(factory, tmp_path, *, private: bool) -> tuple[str, int]:
    """跑一轮，交回会话开场时听到的全部（系统提示词和第一条消息）和席位数。"""
    screen = Screen()
    svc = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=factory,
        compute=ComputePool([screen.runtime], screen.name),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        await registered(session, "u")
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
        await write_overview(session, project.id, DOC, "u")
        await session.commit()
    async with factory() as session:
        _, seats = await TopicMemberService(session).list_for_topic(topic_id)
    async for _ in svc.converse(
        topic_id=topic_id, author="u", content="说说进展", summon=True
    ):
        pass
    await settle_turn(svc, topic_id)
    assert screen.last_system_prompt is not None
    return f"{screen.last_system_prompt}\n\n{screen.last_prompt}", seats


async def test_a_dm_is_told_the_overview_a_room_is_told(client, tmp_path):
    """合同：项目总览，两席的私聊和两席的普通房间一模一样。"""
    private_prompt, private_seats = client.portal.call(
        lambda: _prompt_of(client.test_request_factory, tmp_path, private=True)
    )
    room_prompt, room_seats = client.portal.call(
        lambda: _prompt_of(client.test_request_factory, tmp_path, private=False)
    )

    # 前提：两边都是两席，比的才是「私聊 vs 普通」而不是「两席 vs 多席」。
    assert private_seats == 2
    assert room_seats == 2

    for prompt in (private_prompt, room_prompt):
        assert "## 项目是什么" in prompt
        assert DOC in prompt
