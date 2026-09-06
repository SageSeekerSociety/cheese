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
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from tests.conftest import StubChannel, settle_turn, stub_compute


class RecordingScreen(StubChannel):
    """记下每一轮拿到的 prompt；第一轮的会话停在半路，直到 `release` 放行。

    停住的那一轮就是「轮次运行中」这个窗口 —— 期间落库的人类消息，时间戳都排在
    它那条 AI 回复之前。
    """

    def __init__(self) -> None:
        super().__init__()
        self.prompts: list[str] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self._answering: set[asyncio.Task] = set()

    async def send_prompt(
        self, screen: uuid.UUID, prompt: str, images: list[dict] | None = None
    ) -> bool:
        del images
        self.prompts.append(prompt)
        self.last_prompt = prompt
        first = len(self.prompts) == 1
        if first:
            self.started.set()
        task = asyncio.get_running_loop().create_task(
            self._answer(screen, prompt, len(self.prompts), wait=first)
        )
        self._answering.add(task)
        task.add_done_callback(self._answering.discard)
        return True

    async def _answer(
        self, topic_id: uuid.UUID, prompt: str, nth: int, *, wait: bool
    ) -> None:
        if wait:
            await self.release.wait()
        self.starts(topic_id, session_id="s-window")
        self.acknowledges(topic_id, prompt)
        self.stops(topic_id, f"回复 {nth}", session_id="s-window")


async def _service(factory, agent: StubChannel, tmp_path) -> ChatService:
    return ChatService(
        session_factory=factory,
        compute=stub_compute(agent),
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
    agent = RecordingScreen()
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
    await settle_turn(svc, topic_id)

    await asyncio.wait_for(
        _drain(
            svc.converse(topic_id=topic_id, author="u3", content="继续", summon=True)
        ),
        5,
    )
    await settle_turn(svc, topic_id)

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
    agent = RecordingScreen()
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
    """症状三：两人同时 @，第二轮**白跑** —— 数轮数，不看内容。

    会话还在干活的时候，后来的话是**塞进那个会话**的，不是另开一轮。所以两句都
    到了，到的是同一个会话，而只有最先那一轮真正开了工。
    """
    factory = client.test_factory  # type: ignore[attr-defined]
    agent = RecordingScreen()
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
    await settle_turn(svc, topic_id)
    frames1 = await asyncio.wait_for(turn1, 5)
    frames2 = await asyncio.wait_for(turn2, 5)
    await settle_turn(svc, topic_id)

    # 两句都到了，各一次，顺序就是说话的顺序。
    assert agent.prompts == [
        "[u0]: 开工",
        "[u1]: A 怎么办",
        "[u2]: B 也一起",
    ]
    # ……但后面两句都是塞进已经在跑的那个会话的：它们没有开出自己的轮次，
    # 所以一个 `prompt_delivered` 都不该出现在这两条流里。
    delivered = [
        f["type"] == "prompt_delivered" for fs in (frames1, frames2) for f in fs
    ]
    assert sum(delivered) == 0, "已经在跑的会话收得下这句话，不该再开一轮"


@pytest.mark.anyio
async def test_resume_turn_still_speaks_as_the_platform(client, tmp_path):
    """边界守卫：真的没人说话的轮次，兜底**必须**保留。

    上面三条修的是「有人说过话，却被当成平台指令」。反过来的一半不能跟着改掉：
    resume / kickoff / 结论回流本来就没有人类块，pending 空是正常状态，既不该被
    症状三的早退当成冗余轮吞掉，也仍旧要顶着平台抬头交给芝士。
    """
    factory = client.test_factory  # type: ignore[attr-defined]
    agent = RecordingScreen()
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
    await settle_turn(svc, topic_id)

    # 再来一轮没人说话的重发：pending 必然是空的。
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
