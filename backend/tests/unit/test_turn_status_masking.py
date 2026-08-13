"""一句闲话不能把正在跑的那一轮说成「停了」(#349).

The property under test is a question, not a data layout: **"is this topic
running right now?"** must be answered by the turn that is running — never by
whichever record happens to be the newest one.

Why that distinction is the whole bug: a turn that ends in 0.1 seconds (someone
posting a message nobody summoned 芝士 for; a turn refused on credits) is
*created after* the long turn it says nothing about. Reading the newest record
then reports a busy topic as stopped, and `done / out=none / tools=0` is exactly
what a turn dying at birth looks like — a human's watcher was fooled by it, and
so was the human (2026-08-13, topic bdf6626e).

So these tests never assert an ordering or a field: they build the two-turn
situation and ask the public API what the topic is doing.
"""

import asyncio
import uuid

import pytest

from app.domain.agent.runtime import InProcessBroker, TurnRunner


class Chat:
    """Stand-in ChatService. A summoned turn parks until released — that is the
    long-running turn every test here needs to still be visible. A post-only
    pass (summon=False) behaves like the real one: land the block, end."""

    def __init__(self, policy: dict | None = None) -> None:
        self.policy = policy
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.posted: list[str] = []
        self.system_events: list[str] = []

    async def turn_policy(self, topic_id: uuid.UUID) -> dict | None:
        return self.policy

    async def post_system_event(
        self,
        topic_id: uuid.UUID,
        content: str,
        turn_id: uuid.UUID | None = None,
        meta: dict | None = None,
    ) -> dict:
        self.system_events.append(content)
        return {"content": content}

    async def converse(self, **kwargs):
        if not kwargs.get("summon", True):
            self.posted.append(kwargs["content"])
            yield {"type": "user_block", "block": {"content": kwargs["content"]}}
            yield {"type": "done"}
            return
        yield {"type": "assistant_block", "block": {"content": "在做了"}}
        self.started.set()
        await self.release.wait()
        yield {"type": "done"}


async def _until(cond, timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not cond():
            await asyncio.sleep(0.01)


def _runner(policy: dict | None = None) -> tuple[TurnRunner, InProcessBroker, Chat]:
    broker = InProcessBroker()
    return TurnRunner(broker, turn_timeout_s=5.0), broker, Chat(policy)


async def _parked_turn(runner: TurnRunner, chat: Chat, topic: uuid.UUID) -> str:
    """Start a summoned turn and leave it running. Returns its turn id."""
    runner.submit(chat, topic, author="u1", content="开工", summon=True)
    await asyncio.wait_for(chat.started.wait(), 2)
    turn = runner.topic_turn(topic)
    assert turn is not None and turn["status"] == "running"
    return turn["turn_id"]


@pytest.mark.anyio
async def test_a_message_typed_mid_turn_does_not_report_the_topic_as_stopped():
    """主菜：芝士在干活，房间里有人说了句不 @ 它的话 —— 话题还是「在跑」。"""
    runner, _, chat = _runner()
    topic = uuid.uuid4()
    working = await _parked_turn(runner, chat, topic)

    runner.submit(chat, topic, author="u2", content="我插一句", summon=False)
    await _until(lambda: chat.posted == ["我插一句"])
    await _until(lambda: runner.active_turns() == 1)  # the post is fully done

    turn = runner.topic_turn(topic)
    assert turn is not None
    assert turn["status"] == "running", "说句话把正在跑的那一轮说成停了"
    assert turn["turn_id"] == working
    assert topic in runner.running_topic_ids()

    # And once the real turn ends, the topic honestly reports itself stopped.
    chat.release.set()
    await _until(lambda: runner.active_turns() == 0)
    assert runner.topic_turn(topic)["status"] == "done"
    assert topic not in runner.running_topic_ids()


@pytest.mark.anyio
async def test_a_turn_that_ends_first_does_not_mask_the_one_still_running():
    """同一条性质，换一种「更晚建立、更早结束」的轮次：额度用完被拒的那一轮。

    这条盯的是病根本身 —— 「取最近一条」这个实现选择。空转轮次只是它最常见的
    触发方式，不是唯一一种。"""
    runner, _, chat = _runner(
        {"project_id": "p", "max_concurrent_turns": 4, "credits_exhausted": False}
    )
    topic = uuid.uuid4()
    working = await _parked_turn(runner, chat, topic)

    # 额度在这一轮跑到一半时用完了：下一次召唤当场被拒，留下一条更晚的记录。
    assert chat.policy is not None
    chat.policy["credits_exhausted"] = True
    runner.submit(chat, topic, author="u2", content="芝士再看看", summon=True)
    await _until(lambda: any("算力额度已用完" in e for e in chat.system_events))
    await _until(lambda: runner.active_turns() == 1)

    turn = runner.topic_turn(topic)
    assert turn is not None
    assert turn["status"] == "running"
    assert turn["turn_id"] == working
    assert topic in runner.running_topic_ids()

    chat.release.set()
    await _until(lambda: runner.active_turns() == 0)


@pytest.mark.anyio
async def test_a_cheese_write_mid_turn_still_finds_its_continuation():
    """幂等命名空间也是同一条性质：跑着的那一轮通过 cheese 写平台状态时，
    必须还能认出自己属于哪个 continuation —— 否则它已经做过的副作用在续跑后
    会被重做一遍。"""
    runner, _, chat = _runner()
    topic = uuid.uuid4()
    working = await _parked_turn(runner, chat, topic)
    assert str(runner.continuation_for(topic)) == working

    runner.submit(chat, topic, author="u2", content="顺嘴一提", summon=False)
    await _until(lambda: chat.posted == ["顺嘴一提"])
    await _until(lambda: runner.active_turns() == 1)

    assert str(runner.continuation_for(topic)) == working

    chat.release.set()
    await _until(lambda: runner.active_turns() == 0)
    # Nothing running → no namespace to dedup against, as before.
    assert runner.continuation_for(topic) is None


@pytest.mark.anyio
async def test_a_plain_message_is_not_a_turn_at_all():
    """配菜 (a)：一条 done、零输出、summon=false 的记录压根不该产生。"""
    runner, _, chat = _runner()
    topic = uuid.uuid4()

    runner.submit(chat, topic, author="u", content="队友我们今晚开会", summon=False)
    await _until(lambda: chat.posted == ["队友我们今晚开会"])
    await _until(lambda: runner.active_turns() == 0)

    assert runner.topic_turn(topic) is None
    assert runner.recent_turns() == []
    assert topic not in runner.running_topic_ids()


@pytest.mark.anyio
async def test_a_plain_message_does_not_end_the_running_turns_stream():
    """重放缓冲/正在思考指示器也不能被一句话关掉：`done` 的意思是「这一轮结束
    了」，而所有在看这个房间的人的浏览器都会照做。"""
    runner, broker, chat = _runner()
    topic = uuid.uuid4()
    channel = str(topic)
    await _parked_turn(runner, chat, topic)
    assert broker.in_flight(channel) is True

    async with broker.subscribe(channel) as q:
        runner.submit(chat, topic, author="u2", content="我插一句", summon=False)
        frame = await asyncio.wait_for(q.get(), 2)
        assert frame["type"] == "user_block"  # 消息照样实时送到每个人眼前
        await _until(lambda: runner.active_turns() == 1)
        assert q.empty(), "跟在消息后面的 `done` 会把别人的「正在看…」关掉"

    assert broker.in_flight(channel) is True, "正在跑的那一轮的重放缓冲被清掉了"

    chat.release.set()
    await _until(lambda: runner.active_turns() == 0)
    assert broker.in_flight(channel) is False


@pytest.mark.anyio
async def test_a_plain_message_on_an_idle_topic_leaves_it_idle():
    """反过来也要成立：闲着的房间里说句话，不能让它看起来「有一轮在跑」。"""
    runner, broker, chat = _runner()
    topic = uuid.uuid4()

    runner.submit(chat, topic, author="u", content="下午好", summon=False)
    await _until(lambda: chat.posted == ["下午好"])
    await _until(lambda: runner.active_turns() == 0)

    assert broker.in_flight(str(topic)) is False
    async with broker.subscribe(str(topic), replay=True) as q:
        assert q.empty()  # 一条已落库的消息不该被当成半截轮次重放给后来者


@pytest.mark.anyio
async def test_a_plain_message_is_not_held_by_the_projects_turn_limit():
    """说话不占算力，也就不该排算力的队 —— 更不该收到「⏳ 排队中」。"""
    runner, _, chat = _runner(
        {"project_id": "p", "max_concurrent_turns": 1, "credits_exhausted": False}
    )
    topic = uuid.uuid4()
    await _parked_turn(runner, chat, topic)  # 唯一的并发位被占住

    runner.submit(chat, topic, author="u2", content="我插一句", summon=False)
    await _until(lambda: chat.posted == ["我插一句"])  # 卡住的话这里超时
    assert chat.system_events == []

    chat.release.set()
    await _until(lambda: runner.active_turns() == 0)


@pytest.mark.anyio
async def test_a_plain_message_lands_without_the_credits_refusal():
    """额度用完了照样能说话，而且不该收到「算力额度已用完」—— 没人召唤谁，
    那条通告说的是一件没发生的事。"""
    runner, _, chat = _runner(
        {"project_id": "p", "max_concurrent_turns": 2, "credits_exhausted": True}
    )
    topic = uuid.uuid4()

    runner.submit(chat, topic, author="u", content="额度是不是没了", summon=False)
    await _until(lambda: chat.posted == ["额度是不是没了"])
    await _until(lambda: runner.active_turns() == 0)

    assert chat.system_events == []
    assert runner.topic_turn(topic) is None
