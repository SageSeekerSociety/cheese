"""轮次的 prompt 窗口按**归属**划界，不按时间戳位置划界。

一条在轮次运行中到达的人类消息，`created_at` 排在那轮 AI 回复**之前**，所以
「最后一条 AI 消息之后」这个旧窗口会把它切掉。三条同根症状各有各的断言：

1. 消息**缺席**下一轮的 prompt（而且再也回不来 —— 那个下标只会往前走）；
2. 带 @ 的插话被 `platform_prompt` 包成**平台指令**，标签错了；
3. 两人同时 @ 时，第二轮**白跑一轮**，把已经答过的话重投一遍。

都测行为：只看交到 agent 手上的 prompt、以及跑了几轮。
"""

import asyncio
import uuid

import pytest

from app.domain.agent.chat import PLATFORM_NOTICE, ChatService
from app.domain.agent.service import AgentResult, AgentService
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService


class RecordingAgent(AgentService):
    """记下每一轮拿到的 prompt；第一轮停在半路，直到 `release` 放行。

    停住的那一轮就是「轮次运行中」这个窗口 —— 它握着话题锁，期间落库的人类
    消息，时间戳都排在它那条 AI 回复之前。
    """

    def __init__(self) -> None:
        super().__init__(model="stub")
        self.prompts: list[str] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream_reply(
        self,
        *,
        prompt,
        system_prompt,
        cwd,
        resume_session_id,
        sandbox=None,
        allowed_tools=None,
        **_,
    ):
        self.prompts.append(prompt)
        if len(self.prompts) == 1:
            self.started.set()
            await self.release.wait()
        yield AgentResult(
            text=f"回复 {len(self.prompts)}", session_id="s-window", usage=None
        )


async def _service(factory, agent: AgentService, tmp_path) -> ChatService:
    return ChatService(
        session_factory=factory,
        agent=agent,
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )


async def _new_topic(factory) -> uuid.UUID:
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u0")
        topic = await TopicService(session).create(
            project_id=project.id, title="话题", created_by="u0"
        )
        topic_id: uuid.UUID = topic.id
        await session.commit()
    return topic_id


async def _drain(frames) -> list[dict]:
    return [f async for f in frames]


async def _post_and_queue(svc, topic_id, *, author: str, content: str):
    """@ 一句：消息**立刻**落库（现场必须实时），这一轮排队等锁。

    返回还没跑完的抽取任务 —— 拉走第一帧就够了：那一帧到手时人类块已经在库里，
    时间戳已经排在当前那轮 AI 回复之前，也就是这次要修的场景。
    """
    gen = svc.converse(topic_id=topic_id, author=author, content=content, summon=True)
    first = await gen.__anext__()
    assert first["type"] == "user_block"
    return asyncio.create_task(_drain(gen))


@pytest.mark.anyio
async def test_message_posted_mid_turn_is_not_lost_from_the_next_prompt(
    client, tmp_path
):
    """症状一：轮次运行中到达的消息，**缺席**下一轮的 prompt。"""
    factory = client.test_factory  # type: ignore[attr-defined]
    agent = RecordingAgent()
    svc = await _service(factory, agent, tmp_path)
    topic_id = await _new_topic(factory)

    turn1 = asyncio.create_task(
        _drain(
            svc.converse(topic_id=topic_id, author="u1", content="开工", summon=True)
        )
    )
    await asyncio.wait_for(agent.started.wait(), 5)

    # 芝士还在跑，u2 不 @ 地插一句 —— 落库时间排在 turn1 那条 AI 回复之前。
    await asyncio.wait_for(
        _drain(
            svc.converse(
                topic_id=topic_id,
                author="u2",
                content="顺手看下登录接口",
                summon=False,
            )
        ),
        5,
    )

    agent.release.set()
    await asyncio.wait_for(turn1, 5)

    await asyncio.wait_for(
        _drain(
            svc.converse(topic_id=topic_id, author="u3", content="继续", summon=True)
        ),
        5,
    )

    assert len(agent.prompts) == 2
    # 断言就是「在不在」：旧窗口从 turn1 的 AI 回复起算，这句被切掉且永不再来。
    assert "顺手看下登录接口" in agent.prompts[1]
    assert "[u3]: 继续" in agent.prompts[1]
    # 已经答过的那句不重投。
    assert "开工" not in agent.prompts[1]


@pytest.mark.anyio
async def test_mid_turn_summon_is_not_relabelled_as_a_platform_instruction(
    client, tmp_path
):
    """症状二：真人插话被当成平台指令 —— 内容对了，**标签**错了。"""
    factory = client.test_factory  # type: ignore[attr-defined]
    agent = RecordingAgent()
    svc = await _service(factory, agent, tmp_path)
    topic_id = await _new_topic(factory)

    turn1 = asyncio.create_task(
        _drain(
            svc.converse(topic_id=topic_id, author="u1", content="开工", summon=True)
        )
    )
    await asyncio.wait_for(agent.started.wait(), 5)

    turn2 = await _post_and_queue(
        svc, topic_id, author="u2", content="顺便把 README 也更了"
    )

    agent.release.set()
    await asyncio.wait_for(turn1, 5)
    await asyncio.wait_for(turn2, 5)

    assert len(agent.prompts) == 2
    second = agent.prompts[1]
    # 这是一个人说的话：带说话人标签，且**不能**顶着平台权威的抬头。
    assert "[u2]: 顺便把 README 也更了" in second
    assert PLATFORM_NOTICE not in second


@pytest.mark.anyio
async def test_two_simultaneous_summons_run_one_turn_not_two(client, tmp_path):
    """症状三：两人同时 @，第二轮**白跑** —— 数轮数，不看内容。"""
    factory = client.test_factory  # type: ignore[attr-defined]
    agent = RecordingAgent()
    svc = await _service(factory, agent, tmp_path)
    topic_id = await _new_topic(factory)

    # 一轮先握着锁，制造「两条 @ 都在排队」的窗口。
    turn0 = asyncio.create_task(
        _drain(
            svc.converse(topic_id=topic_id, author="u0", content="开工", summon=True)
        )
    )
    await asyncio.wait_for(agent.started.wait(), 5)

    turn1 = await _post_and_queue(svc, topic_id, author="u1", content="A 怎么办")
    turn2 = await _post_and_queue(svc, topic_id, author="u2", content="B 也一起")

    agent.release.set()
    await asyncio.wait_for(turn0, 5)
    frames1 = await asyncio.wait_for(turn1, 5)
    frames2 = await asyncio.wait_for(turn2, 5)

    # 先拿到锁的那一轮把两条合并答掉；后一轮无事可做，直接收工。
    assert len(agent.prompts) == 2, "已经答过的消息不该再触发一轮"
    merged = agent.prompts[1]
    assert "A 怎么办" in merged and "B 也一起" in merged

    replied = [f["type"] == "assistant_block" for fs in (frames1, frames2) for f in fs]
    assert sum(replied) == 1  # 只有一轮真的回了话

    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(topic_id)
    ai_messages = [
        b
        for b in rows
        if b.author_type == AuthorType.ai and b.kind == BlockKind.message
    ]
    assert len(ai_messages) == 2  # turn0 一条 + 合并轮一条，没有多余的第三条


@pytest.mark.anyio
async def test_resume_turn_still_speaks_as_the_platform(client, tmp_path):
    """边界守卫：真的没人说话的轮次，兜底**必须**保留。

    上面三条修的是「有人说过话，却被当成平台指令」。反过来的一半不能跟着改掉：
    resume / kickoff / 结论回流本来就没有人类块，pending 空是正常状态，既不该被
    症状三的早退当成冗余轮吞掉，也仍旧要顶着平台抬头交给芝士。
    """
    factory = client.test_factory  # type: ignore[attr-defined]
    agent = RecordingAgent()
    agent.release.set()  # 这条不需要卡住轮次
    svc = await _service(factory, agent, tmp_path)
    topic_id = await _new_topic(factory)

    # 先跑一轮真人召唤，把话题里唯一的人类块盖上 consumed 戳。
    await asyncio.wait_for(
        _drain(
            svc.converse(topic_id=topic_id, author="u1", content="开工", summon=True)
        ),
        5,
    )

    # 再来一轮没人说话的续跑：pending 必然是空的。
    await asyncio.wait_for(
        _drain(
            svc.converse(
                topic_id=topic_id,
                author="system",
                content="从上一轮的断点继续",
                is_resume=True,
            )
        ),
        5,
    )

    assert len(agent.prompts) == 2, "没人说话的轮次不是冗余轮，不能被吞掉"
    resumed = agent.prompts[1]
    assert PLATFORM_NOTICE in resumed  # 兜底还在
    assert "从上一轮的断点继续" in resumed
    assert "[system]:" not in resumed  # 平台不冒充说话人
