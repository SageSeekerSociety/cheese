"""项目指定骨架那条路径的执行侧：真跑的和记下来的，是同一个骨架（结论 28）。

解析函数自己有单元测试（``tests/unit/test_harness_is_a_deployment_setting.py``），
但它答对了不等于下游照着它跑。会话行按 (房间, agent, 骨架) 落键，所以一轮里
「跑在哪个骨架上」被问两遍、两遍答案不一样，坏法不是报错而是安静地分叉：一条对话
落成两行，下一轮拿着 A 的续接凭证去接 B。

所以这里钉的是执行侧：一个指定了别的骨架的项目，跑在哪个上，就记在哪个名下；房间
那台机器上一个可用的都没有，这一轮压根不开始，房间里说明。
"""

import uuid

import pytest

from app.api import deps as session_turn_deps
from app.core.config import settings
from app.domain.agent.chat import ChatService
from app.domain.agent.harness import CLAUDE_CODE, PI
from app.domain.agent_session.services import AgentSessionService
from app.domain.identity.handles import CHEESE_HANDLE
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from tests.conftest import StubChannel, settle_turn, stub_compute
from tests.integration.conftest import registered


async def _room(factory, project_settings: dict) -> uuid.UUID:
    async with factory() as session:
        await registered(session, "u")
        project = await ProjectService(session).create(name="P", owner_handle="u")
        project.settings = project_settings
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        await session.commit()
        return topic.id


async def _converse(service: ChatService, topic_id: uuid.UUID) -> list[str]:
    frames = [
        frame
        async for frame in service.converse(
            topic_id=topic_id, author="u", content="开工", summon=True
        )
    ]
    return [
        frame["block"]["content"]
        for frame in frames
        if frame.get("type") == "event_block"
    ]


@pytest.mark.anyio
async def test_a_project_whose_harness_this_machine_lacks_runs_the_next_one(
    business_db_factory, tmp_path, monkeypatch
):
    """项目指定了 pi，这台机器只挂着 Claude Code：这一轮在 Claude Code 上跑，也只
    记在 Claude Code 名下——不会以 pi 的名义记下一条 Claude Code 的会话。"""
    factory = business_db_factory
    screen = StubChannel()
    service = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=factory,
        compute=stub_compute(screen),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    monkeypatch.setattr(settings, "agent_harnesses", [CLAUDE_CODE, PI])
    topic_id = await _room(factory, {"harness": PI})

    await _converse(service, topic_id)
    assert screen.last_prompt is not None
    screen.starts(topic_id, session_id="sid-1")
    screen.acknowledges(topic_id, screen.last_prompt)
    screen.says(topic_id, "好")
    screen.stops(topic_id, "好", session_id="sid-1")
    await settle_turn(service, topic_id)
    await screen.runtime.stop_listening()

    async with factory() as session:
        sessions = AgentSessionService(session)
        assert (
            await sessions.resume_token(topic_id, CHEESE_HANDLE, harness=CLAUDE_CODE)
            == "sid-1"
        )
        assert await sessions.resume_token(topic_id, CHEESE_HANDLE, harness=PI) is None


@pytest.mark.anyio
async def test_a_room_whose_machine_runs_none_of_the_listed_harnesses_does_not_run(
    business_db_factory, tmp_path, monkeypatch
):
    """部署只列了 pi，这台机器只挂着 Claude Code：这一轮不开始，房间里说明——不改
    用部署没列的那个跑。"""
    factory = business_db_factory
    screen = StubChannel()
    service = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=factory,
        compute=stub_compute(screen),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    topic_id = await _room(factory, {})
    monkeypatch.setattr(settings, "agent_harnesses", [PI])

    said = await _converse(service, topic_id)

    assert any("没有部署 pi" in text for text in said), said
    # 一句 prompt 都没有交出去：不是「跑了但结果不要」，是没跑。
    assert screen.last_prompt is None
    async with factory() as session:
        sessions = AgentSessionService(session)
        for harness in (PI, CLAUDE_CODE):
            token = await sessions.resume_token(
                topic_id, CHEESE_HANDLE, harness=harness
            )
            assert token is None, harness
