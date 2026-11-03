"""压缩过上下文的会话，下一条输入重新带上整份现状。

开场快照（成员、频道、项目目标、记忆索引……）在对话历史里，只在开场说一次，之后只补
变了的段落。压缩把那段历史换成一段摘要：没变的段落从窗口里没了，而平台记着「告诉过」，
就再也不会说。一条写在团队记忆里的项目规矩，就这样在同一个座位上一次被照做、下一次
被漏掉。压缩失败什么都没丢，不用重说。
"""

import uuid

import pytest

from app.api import deps as session_turn_deps
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.agent.turn.store.session_inputs import forget_opening, remember_opening
from app.domain.agent_session.services import AgentSessionService
from app.domain.topic.services import TopicService
from tests.conftest import StubChannel, settle_turn
from tests.integration.conftest import project_service, registered

pytestmark = pytest.mark.anyio

#: 开场快照里一段跟踪着的现状（`TRACKED_SECTIONS`），这个项目里它一直没变。
_SNAPSHOT = "## 这个项目的产物清单"


class Screen(StubChannel):
    async def send_prompt(self, screen: uuid.UUID, prompt: str) -> bool:
        self.starts(screen, session_id="compacts")
        self.stops(screen, "Done.", session_id="compacts")
        return True


async def _prompts_around_a_compaction(factory, tmp_path, result: str) -> list[str]:
    """两轮，会话自己压缩一次（``result`` 是骨架报的结果），再一轮；交回每一轮
    发出去的消息。项目从头到尾没变。"""
    screen = Screen()
    svc = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=factory,
        compute=ComputePool([screen.runtime]),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        await registered(session, "u")
        project = await project_service(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="Work", created_by="u"
        )
        await session.commit()
        topic_id = topic.id

    seen: list[str] = []

    async def turn(content: str) -> None:
        async for _ in svc.converse(
            topic_id=topic_id, author="u", content=content, summon=True
        ):
            pass
        await settle_turn(svc, topic_id)
        seen.append(screen.last_prompt or "")

    await turn("开始吧")
    await turn("接着做")
    # 会话空闲时压缩（`cheese_session(action=compact)` 写下的 `/compact`，或者骨架自己），按
    # Claude Code 报压缩的那两条记录。
    screen.record(topic_id, type="system", subtype="status", status="compacting")
    extra = {"compact_error": "too long"} if result == "failed" else {}
    screen.record(
        topic_id,
        type="system",
        subtype="status",
        status=None,
        compact_result=result,
        **extra,
    )
    await settle_turn(svc, topic_id)
    await turn("再接着做")
    return seen


async def test_after_a_compaction_the_next_input_tells_the_whole_state(
    client, tmp_path
):
    first, second, third = client.portal.call(
        lambda: _prompts_around_a_compaction(
            client.test_request_factory, tmp_path, "success"
        )
    )

    assert _SNAPSHOT in first
    # 什么都没变，没压缩之前不重说。
    assert _SNAPSHOT not in second
    # 压缩之后，窗口里已经没有那份现状了：下一条输入整份带上。
    assert _SNAPSHOT in third, third


async def test_a_failed_compaction_lost_nothing_and_tells_nothing(client, tmp_path):
    first, second, third = client.portal.call(
        lambda: _prompts_around_a_compaction(
            client.test_request_factory, tmp_path, "failed"
        )
    )

    assert _SNAPSHOT in first
    assert _SNAPSHOT not in second
    assert _SNAPSHOT not in third


async def _told_after_a_compaction_mid_send(factory) -> dict | None:
    """A turn reads what its conversation was told and sends only the changes;
    the compaction ends before the turn records what it told."""
    async with factory() as session:
        await registered(session, "u")
        project = await project_service(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="Work", created_by="u"
        )
        await session.commit()
    seat = {"agent_handle": "cheese", "harness": "claude-code"}
    told = {"roster": "a", "memory": "b"}
    await remember_opening(factory, topic_id=topic.id, digests=told, was=None, **seat)

    async with factory() as session:
        read = await AgentSessionService(session).told(
            topic.id, seat["agent_handle"], harness=seat["harness"]
        )
    await forget_opening(factory, conversation_id=topic.id, **seat)
    await remember_opening(
        factory, topic_id=topic.id, digests={**told, "roster": "c"}, was=read, **seat
    )

    async with factory() as session:
        return await AgentSessionService(session).told(
            topic.id, seat["agent_handle"], harness=seat["harness"]
        )


async def test_a_compaction_that_ends_mid_send_is_not_written_over(client):
    told = client.portal.call(
        lambda: _told_after_a_compaction_mid_send(client.test_request_factory)
    )

    # The turn told only what changed; the compaction took the rest out of the
    # window. Nothing may say the rest is still there, so the next input tells
    # the whole state.
    assert told is None
