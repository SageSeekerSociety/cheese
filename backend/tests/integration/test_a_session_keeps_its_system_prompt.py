"""一个会话里系统提示词一字不变，项目变了就在下一轮的消息里补上，只补一次。

系统提示词变了，从变的那个字往后、连同整段对话历史，前缀缓存全部作废；而一条接着
跑的对话，进程空闲退出后用 ``--resume`` 拉起来时会重新读一次系统提示词。所以项目
现状不进系统提示词：新会话的第一条消息带着整份，之后变了的那一段在下一轮补上。
"""

import uuid

import pytest

from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from tests.conftest import StubChannel, settle_turn
from tests.integration.conftest import registered
from tests.support.living_doc import write_doc

pytestmark = pytest.mark.anyio


class Screen(StubChannel):
    async def send_prompt(self, screen: uuid.UUID, prompt: str) -> bool:
        self.starts(screen, session_id="keeps")
        self.stops(screen, "Done.", session_id="keeps")
        return True


async def _three_turns(factory, tmp_path) -> list[tuple[str, str]]:
    """跑三轮，交回每一轮的（系统提示词，发出去的消息）。第一轮之后项目里多了一个
    话题、实况文档也改了；第二轮之后什么都没变。"""
    screen = Screen()
    svc = ChatService(
        session_factory=factory,
        compute=ComputePool([screen.runtime], screen.name),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        await registered(session, "u")
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="Work", created_by="u"
        )
        await session.commit()
        project_id, topic_id = project.id, topic.id

    seen: list[tuple[str, str]] = []

    async def turn(content: str) -> None:
        async for _ in svc.converse(
            topic_id=topic_id, author="u", content=content, summon=True
        ):
            pass
        await settle_turn(svc, topic_id)
        seen.append((screen.last_system_prompt or "", screen.last_prompt or ""))

    await turn("开始吧")
    async with factory() as session:
        await TopicService(session).create(
            project_id=project_id, title="新开的话题", created_by="u"
        )
        await write_doc(session, topic_id, "## 目标\n\n改过的文档", "u", quiet=True)
        await session.commit()
    await turn("接着做")
    await turn("再接着做")
    return seen


async def test_the_system_prompt_stays_and_a_change_is_told_once(client, tmp_path):
    seen = client.portal.call(
        lambda: _three_turns(client.test_request_factory, tmp_path)
    )
    (first_system, first), (second_system, second), (third_system, third) = seen

    # 项目变了，系统提示词一字不变。
    assert first_system == second_system == third_system
    assert "新开的话题" not in second_system
    # 新会话的第一条消息带着整份现状。
    assert "## 现在的情况" in first
    # 下一轮只补变了的那一段，不再把整份说一遍。
    assert "新开的话题" in second
    assert "## 现在的情况" not in second
    # 已经说过了，第三轮不再说。
    assert "新开的话题" not in third
