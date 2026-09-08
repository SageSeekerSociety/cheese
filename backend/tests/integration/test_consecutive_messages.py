"""连发两条消息，前一条不能丢。

一个人打字的常见节奏是「先说事，再补一个 @ 把芝士叫过来」。那是两条消息：
第一条没有 summon，第二条有。规矩是 spec §7.1 所有消息 AI 都会收到 —— 没 @
的那条由下一轮组装 prompt 时的 pending 窗口捎带过去。

但一轮正在跑的时候，带 @ 的那条**并入**正在跑的那一轮，不组装任何 prompt；
而 pending 窗口只有组装 prompt 时才会被读。于是话题只要还在干活，没 @ 的那条
就一直没人递 —— 人看到的现象是：芝士只收到了那个光秃秃的 @，它正在回答的那
句话在它的会话里根本不存在。

方向性正是这么来的：丢的总是**没 @ 的**那条（有正文），留下的总是**带 @ 的**
那条（常常只有一个 @）。
"""

import asyncio
import uuid

import pytest

from app.domain.agent.chat import ChatService
from app.domain.agent.runtime import AgentWorkRunner, InProcessBroker
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from tests.conftest import StubChannel, settle_turn, stub_compute


class WorkingScreen(StubChannel):
    """一个接了活就一直在干、直到被放行才收工的会话。

    第一条 prompt 开一轮；轮次还在跑的时候再写进来的文字是**注入**，和真实屏幕
    一样，传输层分不出这两者，所以这里也只按「有没有活在跑」来分。
    """

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.prompts: list[str] = []
        self.delivered: list[str] = []
        self._answering: set[asyncio.Task] = set()

    async def send_prompt(  # type: ignore[override]
        self, screen: uuid.UUID, prompt: str, images: list[dict] | None = None
    ) -> bool:
        del images
        if self._answering:
            self.delivered.append(prompt)
            return True
        self.prompts.append(prompt)
        self.last_prompt = prompt
        self.started.set()
        task = asyncio.get_running_loop().create_task(self._answer(screen))
        self._answering.add(task)
        task.add_done_callback(self._answering.discard)
        return True

    async def _answer(self, topic_id: uuid.UUID) -> None:
        await self.release.wait()
        self.starts(topic_id, session_id="s1")
        self.stops(topic_id, "done", session_id="s1")


async def _until(cond, timeout: float = 5.0) -> None:
    """给异步投递一点时间，但**不**由这里报错。

    超时就安静返回，让紧跟着的 assert 去说话 —— 「屏幕上什么都没有」比一个
    光秃秃的 TimeoutError 有用得多。
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not cond() and loop.time() < deadline:
        await asyncio.sleep(0.01)


async def _a_topic(factory) -> uuid.UUID:
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="讨论", created_by="u"
        )
        topic_id: uuid.UUID = topic.id
        await session.commit()
    return topic_id


def _service(factory, screen: WorkingScreen, tmp_path) -> ChatService:
    return ChatService(
        session_factory=factory,
        compute=stub_compute(screen),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )


@pytest.mark.anyio
async def test_an_unsummoned_message_reaches_the_turn_already_running(client, tmp_path):
    """没 @ 的消息在一轮跑着的时候发出来，也得当场送进那一轮。

    修之前：`submit_message` 对 summon=False 直接落库就返回，什么都不递 ——
    这条消息要等到下一次**组装 prompt** 才会被 pending 窗口捡走，而话题一直在
    干活时那一刻永远不来。
    """
    factory = client.test_factory  # type: ignore[attr-defined]
    screen = WorkingScreen()
    svc = _service(factory, screen, tmp_path)
    runner = AgentWorkRunner(InProcessBroker(), turn_timeout_s=10.0)
    topic_id = await _a_topic(factory)

    # 一轮开起来，并且停在半路（会话还活着）。
    await runner.submit_message(
        svc, topic_id, author="wangchangxin", content="去查一下这条链路", summon=True
    )
    await asyncio.wait_for(screen.started.wait(), 5)

    # 干活途中，有人不带 @ 地说了一句正事。
    await runner.submit_message(
        svc, topic_id, author="wangchangxin", content="直接说你准备怎么改", summon=False
    )

    await _until(lambda: len(screen.delivered) == 1)
    assert [p.split("\n\n", 1)[0] for p in screen.delivered] == [
        "[wangchangxin]: 直接说你准备怎么改"
    ]

    screen.release.set()
    await settle_turn(svc, topic_id)


@pytest.mark.anyio
async def test_a_bare_mention_after_a_message_carries_both_in_order(client, tmp_path):
    """先说事、再补一个光秃秃的 @ —— 两条都要到，且按打字的顺序到。

    这是现场那两次丢消息的原样复现：09:57:04 的正文 + 2 秒后 09:57:07 的纯 @。
    修之前，屏幕上只会出现那个 @。
    """
    factory = client.test_factory  # type: ignore[attr-defined]
    screen = WorkingScreen()
    svc = _service(factory, screen, tmp_path)
    runner = AgentWorkRunner(InProcessBroker(), turn_timeout_s=10.0)
    topic_id = await _a_topic(factory)

    await runner.submit_message(
        svc, topic_id, author="wangchangxin", content="去查一下这条链路", summon=True
    )
    await asyncio.wait_for(screen.started.wait(), 5)

    await runner.submit_message(
        svc, topic_id, author="wangchangxin", content="直接说你准备怎么改", summon=False
    )
    await runner.submit_message(
        svc, topic_id, author="wangchangxin", content="<@cheese>", summon=True
    )

    await _until(lambda: len(screen.delivered) == 2)
    assert [p.split("\n\n", 1)[0] for p in screen.delivered] == [
        "[wangchangxin]: 直接说你准备怎么改",
        "[wangchangxin]: <@cheese>",
    ]

    screen.release.set()
    await settle_turn(svc, topic_id)


@pytest.mark.anyio
async def test_an_unsummoned_message_on_an_idle_topic_starts_nothing(client, tmp_path):
    """话题闲着的时候，没 @ 的消息照旧只是落库 —— 不开轮次，也不写进任何会话。

    「所有消息 AI 都会收到」不等于「所有消息都值得烧一轮算力」：闲着的话题由
    下一次召唤的 pending 窗口把它捎上，那条路本来就是通的，不能被这次修改改成
    见人就递。
    """
    factory = client.test_factory  # type: ignore[attr-defined]
    screen = WorkingScreen()
    svc = _service(factory, screen, tmp_path)
    runner = AgentWorkRunner(InProcessBroker(), turn_timeout_s=10.0)
    topic_id = await _a_topic(factory)

    await runner.submit_message(
        svc, topic_id, author="wangchangxin", content="先记一句", summon=False
    )
    await asyncio.sleep(0.1)

    assert screen.prompts == []
    assert screen.delivered == []


@pytest.mark.anyio
async def test_the_next_prompt_still_carries_an_unsummoned_message(client, tmp_path):
    """闲着时攒下的那条没 @ 的消息，必须出现在下一轮的 prompt 里。"""
    factory = client.test_factory  # type: ignore[attr-defined]
    screen = WorkingScreen()
    svc = _service(factory, screen, tmp_path)
    runner = AgentWorkRunner(InProcessBroker(), turn_timeout_s=10.0)
    topic_id = await _a_topic(factory)

    await runner.submit_message(
        svc, topic_id, author="wangchangxin", content="先记一句", summon=False
    )
    await runner.submit_message(
        svc, topic_id, author="wangchangxin", content="<@cheese>", summon=True
    )
    await asyncio.wait_for(screen.started.wait(), 5)

    assert len(screen.prompts) == 1
    assert "先记一句" in screen.prompts[0]
    assert "<@cheese>" in screen.prompts[0]

    screen.release.set()
    await settle_turn(svc, topic_id)
