"""项目盖过部署那条路径的执行侧：真跑的和记下来的，是同一个骨架（结论 28）。

解析函数自己有单元测试（``tests/unit/test_harness_is_a_deployment_setting.py``），
但它答对了不等于下游照着它跑。会话行按 (房间, agent, 骨架) 落键，所以一轮里
「跑在哪个骨架上」被问两遍、两遍答案不一样，坏法不是报错而是安静地分叉：一条对话
落成两行，下一轮拿着 A 的续接凭证去接 B。

所以这里钉的是执行侧：一个设成别的骨架的项目，要么跑在那个骨架上，要么这一轮压根
不开始——不会改用部署那个骨架跑一轮，再把结果记到项目那个骨架名下。
"""

import uuid

import pytest

from app.core.config import settings
from app.domain.agent.chat import ChatService
from app.domain.agent.harness import deployment_harness
from app.domain.agent_session.services import AgentSessionService
from app.domain.identity.handles import CHEESE_HANDLE
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from tests.conftest import StubChannel, stub_compute


@pytest.mark.anyio
async def test_a_project_that_asks_for_a_harness_nobody_deployed_does_not_run(
    business_db_factory, tmp_path, monkeypatch
):
    """这台机器上没有 codex，那这一轮就不开始——不改用 claude-code 跑。

    换成 claude-code 跑的那一版没有任何地方会红：屏幕照常答，房间照常收到回复，
    只有会话行悄悄落在 codex 那一行上，而说那句话的是 claude-code。
    """
    factory = business_db_factory
    screen = StubChannel()
    service = ChatService(
        session_factory=factory,
        compute=stub_compute(screen),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    # codex 既不说网关那套话也拿不到订阅凭据，所以运营者不点名的话它一个模型都指
    # 不到，这一轮会早在挑模型时就停下——那验的就不是这里要验的东西了。
    monkeypatch.setattr(settings, "agent_harness_models", {"codex": ["gpt-5-codex"]})
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        project.settings = {"harness": "codex", "default_model": "gpt-5-codex"}
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        topic_id: uuid.UUID = topic.id
        await session.commit()

    frames = [
        frame
        async for frame in service.converse(
            topic_id=topic_id, author="u", content="开工", summon=True
        )
    ]

    said = [
        frame["block"]["content"]
        for frame in frames
        if frame.get("type") == "event_block"
    ]
    assert any("没有部署 codex" in text for text in said), said
    # 一句 prompt 都没有交出去：不是「跑了但结果不要」，是没跑。
    assert screen.last_prompt is None
    # 两行都是空的——既没有以 codex 的名义记下一条 claude-code 的会话，也没有绕开
    # 项目的设置、拿部署那个骨架把这一轮跑掉。
    async with factory() as session:
        sessions = AgentSessionService(session)
        for harness in ("codex", deployment_harness()):
            token = await sessions.resume_token(
                topic_id, CHEESE_HANDLE, harness=harness
            )
            assert token is None, harness
