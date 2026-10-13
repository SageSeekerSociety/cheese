"""AgentWorkRunner admission: project concurrency gate + credit exhaustion (§9.1).

Functional tests against a fake ChatService: the runner must (a) run at most
max_concurrent_turns work items per project at once, queueing the rest FIFO with a
visible "排队中" system event, and (b) refuse work outright when the
project's compute credits are exhausted — landing the human's message but
posting the platform's exhaustion event instead of running the agent.
"""

import asyncio
import uuid

import pytest

from app.domain.agent.realtime.broker import InProcessBroker
from app.domain.agent.runtime import AgentWorkRunner, addressed_to_agent
from tests.support.hang import HANG_S
from tests.support.work_chat import WorkChat
from tests.turn_log import a_topic


class _NothingStored:
    """A database that holds none of the stand-in's messages: they were never
    written, so asking where one is answered finds nothing."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, *args, **kwargs):
        return None


class FakeChat(WorkChat):
    @property
    def human_messages(self):
        return self

    """Controllable stand-in for ChatService: converse turns block until
    released, so tests can observe concurrency and queue order."""

    def __init__(self, policy: dict | None, session_factory=None):
        self.policy = policy
        # Where the runner opens each turn's interval — a real ChatService
        # carries the database, so a stand-in that runs turns carries it too.
        self.session_factory = session_factory or _NothingStored
        self.system_events: list[str] = []
        self.system_event_meta: list[dict | None] = []
        self.running = 0
        self.max_running = 0
        self.converse_calls: list[dict] = []
        self.release = asyncio.Event()

    async def work_policy(
        self, topic_id: uuid.UUID, agent_instance_id: uuid.UUID | None = None
    ) -> dict | None:
        return self.policy

    async def post_system_event(
        self,
        topic_id: uuid.UUID,
        content: str,
        turn_id: uuid.UUID | None = None,
        meta: dict | None = None,
    ) -> dict:
        self.system_events.append(content)
        self.system_event_meta.append(meta)
        return {"content": content, "meta": meta}

    def has_running_turn(
        self, topic_id: uuid.UUID, agent_handle: str | None = None
    ) -> bool:
        return False

    async def thread_at(self, topic_id: uuid.UUID):
        """这些用例说的都是房间：一条活没有轮次可以被准入，也没有轮次可以排队。"""
        return None

    async def post_user_message(self, topic_id, **kwargs):
        self.converse_calls.append({"received": True, **kwargs})
        ids = []
        payloads = []
        # 落库那一刻解析出这条消息点了谁的名 —— 真实的 ChatService 做的就是这件事，
        # 而「这一轮跑不跑」从此只看它。
        recipient = {
            "handle": "cheese-seat",
            "mentioned": "<@cheese-seat>" in (kwargs.get("content") or ""),
        }
        if kwargs["content"]:
            block_id = uuid.uuid4()
            ids.append(block_id)
            payloads.append(
                {
                    "id": str(block_id),
                    "content": kwargs["content"],
                    "meta": {"agent_recipient": recipient},
                }
            )
        for attachment in kwargs.get("attachments") or []:
            block_id = uuid.uuid4()
            ids.append(block_id)
            payloads.append(
                {
                    "id": str(block_id),
                    "content": attachment["path"],
                    "kind": "attachment",
                    "meta": {"agent_recipient": recipient},
                }
            )
        return payloads, ids[0], ids, False

    async def merge_into_running_turn(self, *args, **kwargs):
        return None

    def replaying(self, topic_id):
        return None

    async def converse(self, **kwargs):
        self.converse_calls.append(kwargs)
        if not kwargs.get("summon", True):
            # 没点到谁 = 只落库不起轮次（用来落下一条被拒的消息）。
            yield {"type": "user_block", "block": {"content": kwargs["content"]}}
            yield {"type": "done"}
            return
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            await self.release.wait()
            yield {"type": "done"}
        finally:
            self.running -= 1


@pytest.mark.anyio
async def test_slow_agent_subscriber_does_not_block_receive_and_preserves_order():
    class SlowDelivery(FakeChat):
        def __init__(self):
            super().__init__(None)
            self.entered = asyncio.Event()
            self.unblock = asyncio.Event()
            self.delivered = []

        async def merge_into_running_turn(self, topic, ids, content, *args, **kwargs):
            if content == "first":
                self.entered.set()
                await self.unblock.wait()
            self.delivered.append(content)
            return True

    chat = SlowDelivery()
    runner, broker = _runner()
    topic = uuid.uuid4()
    try:
        async with broker.subscribe(str(topic)) as browser:
            await runner.receive_message(chat, topic, author="u", content="first")
            await chat.entered.wait()
            assert (await browser.get())["block"]["content"] == "first"
            await runner.receive_message(chat, topic, author="u", content="second")
            assert (await browser.get())["block"]["content"] == "second"
            assert chat.delivered == []
        # Delivery is owned by the subscriber even after the browser leaves.
        chat.unblock.set()
        await runner.drain()
        assert chat.delivered == ["first", "second"]
        async with broker.subscribe(str(topic)):
            await asyncio.sleep(0)
        assert chat.delivered == ["first", "second"]
    finally:
        chat.unblock.set()
        await runner.drain()


@pytest.mark.anyio
async def test_failed_agent_delivery_does_not_stop_next_message():
    class FailedDelivery(FakeChat):
        async def merge_into_running_turn(self, topic, ids, content, *args, **kwargs):
            if content == "first":
                raise RuntimeError("executor unavailable")
            self.converse_calls.append({"delivered": content})
            return True

    chat = FailedDelivery(None)
    runner, broker = _runner()
    topic = uuid.uuid4()
    async with broker.subscribe(str(topic)) as browser:
        for content in ("first", "second"):
            await runner.receive_message(chat, topic, author="u", content=content)
        await runner.drain()
        assert {"delivered": "second"} in chat.converse_calls
        frames = []
        while not browser.empty():
            frames.append(browser.get_nowait())
        assert sum(frame["type"] == "user_block" for frame in frames) == 2
        assert sum(frame["type"] == "error" for frame in frames) == 1


@pytest.mark.anyio
async def test_message_to_another_teammate_starts_its_turn_beside_a_live_one(
    db_factory,
):
    """A 的轮次在跑，点到 B 的消息不等它放锁：B 的轮次当场起，两轮并行。

    一轮锁的只是自己那一席（座位锁在真正的 ChatService 里），broker 这层看得见
    的不变量是：按收件人分钥匙的队列让 B 的消息直接准入，而不是排在 A 的轮次
    后面。这里用阻塞的 A 轮次捏住「A 还活着」，B 的 converse_prepared 必须
    照常跑完。
    """

    class TwoSeats(FakeChat):
        def __init__(self, session_factory):
            super().__init__(None, session_factory)
            self.selected = "cheese-a"
            self.a_started = asyncio.Event()
            self.release_a = asyncio.Event()
            self.finished: list[str | None] = []

        async def post_user_message(self, *args, **kwargs):
            payloads, anchor, ids, duplicate = await super().post_user_message(
                *args, **kwargs
            )
            for payload in payloads:
                payload["meta"] = {
                    "agent_recipient": {
                        "handle": self.selected,
                        "mentioned": "<@cheese-seat>" in (kwargs.get("content") or ""),
                    }
                }
            return payloads, anchor, ids, duplicate

        async def merge_into_running_turn(self, topic, ids, content, *args, **kwargs):
            # A 的轮次活着之后，点到 A 的并得进去；B 没有活轮次，并不进去。
            return self.a_started.is_set() and kwargs.get("recipient_handle") == (
                "cheese-a"
            )

        async def converse_prepared(self, **kwargs):
            recipient = kwargs.get("recipient_handle")
            if recipient == "cheese-a":
                self.a_started.set()
                await self.release_a.wait()
            self.finished.append(recipient)
            yield {"type": "done"}

    chat = TwoSeats(db_factory)
    runner, broker = _runner()
    topic = await a_topic(db_factory)
    try:
        await runner.receive_message(
            chat, topic, author="u", content="<@cheese-seat> A's task"
        )
        await asyncio.wait_for(chat.a_started.wait(), HANG_S)
        chat.selected = "cheese-b"
        await runner.receive_message(
            chat, topic, author="u", content="<@cheese-seat> B's task"
        )
        # A's turn is still held — B's must finish without waiting for it.
        await _until(lambda: "cheese-b" in chat.finished)
        assert "cheese-a" not in chat.finished
    finally:
        chat.release_a.set()
        await runner.drain()


async def _until(cond, timeout: float = HANG_S) -> None:
    async with asyncio.timeout(timeout):
        while not cond():
            await asyncio.sleep(0.01)


async def _frames_through(queue, final_type: str) -> list[dict]:
    frames = []
    async with asyncio.timeout(HANG_S):
        while True:
            frame = await queue.get()
            frames.append(frame)
            if frame["type"] == final_type:
                return frames


def _runner() -> tuple[AgentWorkRunner, InProcessBroker]:
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker, turn_timeout_s=5.0)
    return runner, broker


@pytest.mark.anyio
async def test_concurrency_gate_queues_and_announces_position(db_factory):
    chat = FakeChat(
        {
            "project_id": "proj-1",
            "max_concurrent_turns": 1,
            "credits_exhausted": False,
        },
        db_factory,
    )
    runner, _ = _runner()
    topic = await a_topic(db_factory)

    runner.submit(
        chat,
        topic,
        author="u1",
        content="第一轮",
        addressed=addressed_to_agent("cheese-seat"),
    )
    await _until(lambda: chat.running == 1)

    # Second turn: gate is full → queued, with a visible system event (0 ahead).
    runner.submit(
        chat,
        topic,
        author="u2",
        content="第二轮",
        addressed=addressed_to_agent("cheese-seat"),
    )
    await _until(lambda: len(chat.system_events) == 1)
    assert "排队" in chat.system_events[0]

    # Third turn: one waiter already ahead of it.
    runner.submit(
        chat,
        topic,
        author="u3",
        content="第三轮",
        addressed=addressed_to_agent("cheese-seat"),
    )
    await _until(lambda: len(chat.system_events) == 2)
    assert "前面还有 1 个" in chat.system_events[1]

    # Nothing beyond the gate ran while the first turn held the slot.
    assert chat.max_running == 1
    assert len(chat.converse_calls) == 1

    # Release: all three turns complete, never more than one at a time.
    chat.release.set()
    await _until(lambda: len(chat.converse_calls) == 3 and chat.running == 0)
    await _until(lambda: runner.active_work_count() == 0)
    assert chat.max_running == 1


@pytest.mark.anyio
async def test_concurrency_gate_allows_up_to_limit_without_queueing(db_factory):
    chat = FakeChat(
        {
            "project_id": "proj-2",
            "max_concurrent_turns": 2,
            "credits_exhausted": False,
        },
        db_factory,
    )
    runner, _ = _runner()

    runner.submit(
        chat,
        await a_topic(db_factory),
        author="u",
        content="a",
        addressed=addressed_to_agent("cheese-seat"),
    )
    runner.submit(
        chat,
        await a_topic(db_factory),
        author="u",
        content="b",
        addressed=addressed_to_agent("cheese-seat"),
    )
    await _until(lambda: chat.running == 2)

    # Both run concurrently; no queue event was posted.
    assert chat.system_events == []

    chat.release.set()
    await _until(lambda: runner.active_work_count() == 0)
    assert chat.max_running == 2


@pytest.mark.anyio
async def test_a_turn_waits_for_memory_on_the_session_host_then_runs(
    db_factory, monkeypatch
):
    """A host with no memory for one more session holds the turn, says so in
    the room once, and starts it by itself once memory frees: nobody resends."""
    from app.domain.agent import admission

    monkeypatch.setattr(admission, "RECHECK_S", 0.01)
    chat = FakeChat(
        {
            "project_id": "proj-mem",
            "max_concurrent_turns": 4,
            "credits_exhausted": False,
            "on_session_host": True,
        },
        db_factory,
    )
    memory = asyncio.Event()

    async def host_has_room(_topic):
        return memory.is_set()

    runner = AgentWorkRunner(
        InProcessBroker(), turn_timeout_s=5.0, host_has_room=host_has_room
    )

    runner.submit(
        chat,
        await a_topic(db_factory),
        author="u",
        content="a",
        addressed=addressed_to_agent("cheese-seat"),
    )
    await _until(lambda: len(chat.system_events) == 1)
    await asyncio.sleep(0.1)
    assert chat.running == 0

    memory.set()
    await _until(lambda: chat.running == 1)
    assert len(chat.system_events) == 1

    chat.release.set()
    await _until(lambda: runner.active_work_count() == 0)


@pytest.mark.anyio
async def test_a_turn_that_starts_no_session_there_is_not_held_by_the_host(
    db_factory,
):
    """The host's memory holds only turns whose session starts there: a turn
    with no backend on this deployment starts no session and goes on to say so."""
    chat = FakeChat(
        {
            "project_id": "proj-elsewhere",
            "max_concurrent_turns": 4,
            "credits_exhausted": False,
            "on_session_host": False,
        },
        db_factory,
    )

    async def host_has_room(_topic):
        return False

    runner = AgentWorkRunner(
        InProcessBroker(), turn_timeout_s=5.0, host_has_room=host_has_room
    )

    runner.submit(
        chat,
        await a_topic(db_factory),
        author="u",
        content="a",
        addressed=addressed_to_agent("cheese-seat"),
    )
    await _until(lambda: chat.running == 1)
    assert chat.system_events == []

    chat.release.set()
    await _until(lambda: runner.active_work_count() == 0)


@pytest.mark.anyio
async def test_exhausted_credits_refuses_turn_but_lands_message():
    chat = FakeChat(
        {
            "project_id": "proj-3",
            "max_concurrent_turns": 2,
            "credits_exhausted": "本月额度已用完，11月1日重置。",
        }
    )
    runner, broker = _runner()
    topic = uuid.uuid4()

    frames = []
    async with broker.subscribe(str(topic)) as q:
        runner.submit(
            chat,
            topic,
            author="u1",
            content="还在吗",
            addressed=addressed_to_agent("cheese-seat"),
        )
        async with asyncio.timeout(2.0):
            while True:
                frame = await q.get()
                frames.append(frame)
                if frame["type"] == "error":
                    break

    # The human's message landed (posting is free), via a post-only pass.
    assert [f["type"] for f in frames] == ["user_block", "event_block", "error"]
    assert len(chat.converse_calls) == 1
    assert chat.converse_calls[0]["summon"] is False
    # The agent never ran.
    assert chat.max_running == 0
    # The refusal is the PLATFORM's structured copy, in the topic 现场.
    # It says when the credits come back.
    assert any("11月1日重置" in e for e in chat.system_events)
    assert "11月1日重置" in frames[-1]["message"]
    await _until(lambda: runner.active_work_count() == 0)


@pytest.mark.anyio
async def test_a_refused_platform_delivery_does_not_land_as_someone_talking():
    """算力耗尽时，平台自己写的那句提示不会被当成一条人发的消息落进房间。

    「有人说过话」才补落那一条消息。平台的几条投递（机器接入、环境修好、记忆整理）
    作者是 `system`、正文是平台写的一段提示，既不 resume 也不带 nudge —— 当成人话
    补落，房间里就会出现一条谁也没打过的用户消息。
    """
    chat = FakeChat(
        {
            "project_id": "proj-5",
            "max_concurrent_turns": 1,
            "credits_exhausted": True,
        }
    )
    runner, broker = _runner()
    topic = uuid.uuid4()

    frames = []
    async with broker.subscribe(str(topic)) as q:
        runner.submit(
            chat,
            topic,
            author="system",
            content="Cloud machine is ready; continue the pending input.",
            addressed=addressed_to_agent("cheese-seat"),
        )
        async with asyncio.timeout(2.0):
            while True:
                frame = await q.get()
                frames.append(frame)
                if frame["type"] == "error":
                    break

    assert [f["type"] for f in frames] == ["event_block", "error"], frames
    assert chat.converse_calls == [], "平台的提示词被当成一条人发的消息落进了房间"
    assert chat.max_running == 0
    await _until(lambda: runner.active_work_count() == 0)


@pytest.mark.anyio
async def test_unknown_policy_admits_ungated(db_factory):
    chat = FakeChat(None, db_factory)  # topic unknown / unmetered deployment
    runner, _ = _runner()

    runner.submit(
        chat,
        await a_topic(db_factory),
        author="u",
        content="hi",
        addressed=addressed_to_agent("cheese-seat"),
    )
    await _until(lambda: chat.running == 1)
    chat.release.set()
    await _until(lambda: runner.active_work_count() == 0)
    assert len(chat.converse_calls) == 1


@pytest.mark.anyio
async def test_received_message_lands_before_credit_refusal():
    chat = FakeChat(
        {
            "project_id": "proj-4",
            "max_concurrent_turns": 1,
            "credits_exhausted": True,
        }
    )
    runner, broker = _runner()
    topic = uuid.uuid4()

    async with broker.subscribe(str(topic)) as queue:
        await runner.receive_message(
            chat, topic, author="u", content="<@cheese-seat> 这条必须先落库"
        )
        frames = []
        async with asyncio.timeout(2):
            while True:
                frame = await queue.get()
                frames.append(frame)
                if frame["type"] == "error":
                    break

    assert [frame["type"] for frame in frames] == [
        "user_block",
        "event_block",
        "error",
    ]
    # One receive operation, no post-only second pass and no model turn.
    assert len(chat.converse_calls) == 1
    assert chat.converse_calls[0]["received"] is True
    assert chat.converse_calls[0]["content"] == "<@cheese-seat> 这条必须先落库"
    assert "summon" not in chat.converse_calls[0]


@pytest.mark.anyio
async def test_unsummoned_message_never_touches_turn_admission():
    class PostOnly(FakeChat):
        async def work_policy(self, topic_id, agent_instance_id=None):
            raise AssertionError("plain messages do not enter the turn gate")

    chat = PostOnly(None)
    runner, broker = _runner()
    topic = uuid.uuid4()
    async with broker.subscribe(str(topic)) as queue:
        await runner.receive_message(chat, topic, author="u", content="只发消息")
        assert (await queue.get())["type"] == "user_block"
        assert (await queue.get())["type"] == "done"
    await runner.drain()
    await _until(lambda: runner.active_work_count() == 0)


@pytest.mark.anyio
async def test_normal_message_without_live_work_queues_without_fallback_error(
    db_factory,
):
    class Prepared(FakeChat):
        async def converse_prepared(self, **kwargs):
            yield {"type": "done"}

    chat = Prepared(None, db_factory)
    runner, broker = _runner()
    topic = await a_topic(db_factory)

    async with broker.subscribe(str(topic)) as queue:
        await runner.receive_message(
            chat, topic, author="u", content="<@cheese-seat> 正常开工"
        )
        frames = await _frames_through(queue, "turn_finished")

    assert [frame["type"] for frame in frames] == [
        "user_block",
        "turn_started",
        "done",
        "turn_finished",
    ]
    assert chat.system_events == []
    await _until(lambda: runner.active_work_count() == 0)


@pytest.mark.anyio
async def test_a_teammate_whose_handle_is_not_cheese_still_gets_a_turn(db_factory):
    """名册上坐一个 handle 不以 `cheese` 开头的队友，@ 它必须起一轮。

    项目给队友起的名字是随便的（`reviewer`、`planner`…，`AgentInstance.handle`
    允许任意小写串），而「一条投递怎么到达收件人」是按**席位**的命名规矩判的。
    点名时拿实例名去问，这样一个队友就永远不是「靠一轮收到」—— @ 它、和它私聊，
    都只会 merge 进别人正在跑的那一轮，或者直接收工，一轮也起不来。
    """
    instance_id = uuid.uuid4()

    class ATeammateNamedReviewer(FakeChat):
        async def post_user_message(self, topic_id, **kwargs):
            payloads, anchor, ids, duplicate = await super().post_user_message(
                topic_id, **kwargs
            )
            for payload in payloads:
                payload["meta"] = {
                    "agent_recipient": {
                        "instance_id": str(instance_id),
                        "handle": "reviewer",
                        "mentioned": True,
                    }
                }
            return payloads, anchor, ids, duplicate

        async def converse_prepared(self, **kwargs):
            self.converse_calls.append(kwargs)
            yield {"type": "done"}

    chat = ATeammateNamedReviewer(None, db_factory)
    runner, broker = _runner()
    topic = await a_topic(db_factory)

    async with broker.subscribe(str(topic)) as queue:
        await runner.receive_message(chat, topic, author="u", content="@审稿人 看一下")
        frames = await _frames_through(queue, "turn_finished")

    assert [frame["type"] for frame in frames] == [
        "user_block",
        "turn_started",
        "done",
        "turn_finished",
    ]
    # 跑起来的是**这个**队友的一轮：席位只用来寻址，会话那边认的还是实例名。
    assert chat.converse_calls[-1]["recipient_handle"] == "reviewer"
    await _until(lambda: runner.active_work_count() == 0)


@pytest.mark.anyio
async def test_the_wait_before_a_turn_assembles_is_accounted_for(db_factory, caplog):
    """Every other stretch of a turn is timed. The one between a message being
    durably received and its turn starting to assemble was not, and it holds three
    waits that can each be long: the queue behind this recipient's other messages,
    a live-delivery attempt that reaches the database, and the credit and
    concurrency gates. Measured end to end it showed as a gap with nothing in it —
    on a real room, a fifth of the time a person waits before the model is asked
    anything."""

    class Prepared(FakeChat):
        async def converse_prepared(self, **kwargs):
            yield {"type": "done"}

    chat = Prepared(None, db_factory)
    runner, broker = _runner()
    topic = await a_topic(db_factory)

    with caplog.at_level("INFO"):
        async with broker.subscribe(str(topic)) as queue:
            await runner.receive_message(
                chat, topic, author="u", content="<@cheese-seat> 正常开工"
            )
            await _frames_through(queue, "turn_finished")
        await _until(lambda: runner.active_work_count() == 0)

    timings = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("chat_admission_timing")
    ]
    phases = [line.split("phase=")[1].split()[0] for line in timings]
    assert phases == ["message_lock", "live_delivery_declined", "admitted"]
    assert all(f"topic={topic}" in line for line in timings)
    assert all("elapsed_ms=" in line and "unix_ms=" in line for line in timings)
    assert "verdict=ok" in timings[-1]


@pytest.mark.anyio
@pytest.mark.parametrize("delivery_result", [False, None])
async def test_live_delivery_fallback_is_noted_then_runs_normally(
    db_factory,
    delivery_result,
):
    class FailedLiveDelivery(FakeChat):
        def has_running_turn(
            self, topic_id: uuid.UUID, agent_handle: str | None = None
        ) -> bool:
            # 收件人自己那一轮在跑 —— 有人正面问「这个席位在跑吗」时才是真的
            # 递进正在跑的会话。房间级的询问（``agent_handle`` 为空）不该被当成
            # 「这条消息要的一次实时投递」，那正是这条提示以前误报的地方。
            return agent_handle is not None

        async def merge_into_running_turn(self, *args, **kwargs):
            return delivery_result

        async def converse_prepared(self, **kwargs):
            yield {"type": "done"}

    chat = FailedLiveDelivery(None, db_factory)
    runner, broker = _runner()
    topic = await a_topic(db_factory)

    async with broker.subscribe(str(topic)) as queue:
        await runner.receive_message(
            chat, topic, author="u", content="<@cheese-seat> 补充一条"
        )
        frames = await _frames_through(queue, "turn_finished")

    assert [frame["type"] for frame in frames] == [
        "user_block",
        "event_block",
        "turn_started",
        "done",
        "turn_finished",
    ]
    assert frames[1]["block"]["meta"]["event_type"] == "delivery_fallback"
    # The platform took the message on itself: nobody has to do anything.
    assert frames[1]["block"]["meta"]["severity"] == "warn"
    assert frames[1]["block"]["meta"]["who"] == "platform"
    assert len(chat.converse_calls) == 1
    await _until(lambda: runner.active_work_count() == 0)


@pytest.mark.anyio
async def test_another_teammates_turn_is_not_a_lost_delivery(db_factory):
    """一条 @ 了空闲队友的消息，在另一位队友正跑着的房间里，按正常排队走 ——
    不记「未能送达」。提示说的是「本该实时递进一个正在跑的会话却没递成」，而这条
    消息的收件人根本没有在跑的会话：排队就是它本来的路，不是降级。"""

    class RoomBusyRecipientIdle(FakeChat):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.merge_seats: list[str | None] = []

        def has_running_turn(
            self, topic_id: uuid.UUID, agent_handle: str | None = None
        ) -> bool:
            # 房间里有人在跑（不带座位的询问），但收件人自己没在跑（带了座位）。
            return agent_handle is None

        async def merge_into_running_turn(self, *args, **kwargs):
            # 递给哪一位：收了座位就不许挑别人的会话，没有就是房间级的老路。
            self.merge_seats.append(kwargs.get("recipient_handle"))
            return None

    class Prepared(RoomBusyRecipientIdle):
        async def converse_prepared(self, **kwargs):
            yield {"type": "done"}

    chat = Prepared(None, db_factory)
    runner, broker = _runner()
    topic = await a_topic(db_factory)

    async with broker.subscribe(str(topic)) as queue:
        await runner.receive_message(
            chat, topic, author="u", content="<@cheese-seat> 补充一条"
        )
        frames = await _frames_through(queue, "turn_finished")

    kinds = [frame["type"] for frame in frames]
    assert "event_block" not in kinds, [f.get("block") for f in frames]
    assert chat.system_events == []
    # 递给谁就得是谁：收件人的席位，不是「房间里碰巧在跑的任何一个」。
    assert chat.merge_seats == ["cheese-seat"]
    assert kinds == ["user_block", "turn_started", "done", "turn_finished"]
    await _until(lambda: runner.active_work_count() == 0)


@pytest.mark.anyio
async def test_receipted_mid_session_message_has_no_second_done():
    class MergeIntoLive(FakeChat):
        def __init__(self):
            super().__init__(None)
            self.merged: tuple | None = None

        async def work_policy(self, topic_id, agent_instance_id=None):
            raise AssertionError("a delivered mid-turn message needs no new turn")

        async def merge_into_running_turn(self, *args, **kwargs):
            self.merged = args
            return True

    chat = MergeIntoLive()
    runner, broker = _runner()
    topic = uuid.uuid4()
    await broker.publish(
        str(topic), {"type": "turn_started", "turn_id": "already-running"}
    )

    async with broker.subscribe(str(topic)) as queue:
        await runner.receive_message(
            chat, topic, author="u", content="<@cheese-seat> 补充一条"
        )
        frames = []
        async with asyncio.timeout(2):
            while len(frames) < 1:
                frames.append(await queue.get())
        # Waited for the delivery itself, not for a frame count. The second
        # frame this used to wait on was the mark, and waiting for it was
        # also what gave the merge time to happen — a coincidence that goes
        # away with it.
        await _until(lambda: chat.merged is not None)

    # Just the message. Delivery into the live session says the write was
    # taken, not that the session read it, so no mark rides this path any
    # more: `chat.arm_seen_receipt` names the block here and
    # `chat.confirm_prompt_receipt` places the 👀 when the harness reports
    # that the session has the text.
    assert [frame["type"] for frame in frames] == ["user_block"]
    assert chat.merged is not None
    assert chat.merged[2:] == ("<@cheese-seat> 补充一条", "u", None)
    assert broker.active_turn_ids(str(topic)) == ["already-running"]
    assert runner.active_work_count() == 1
    await broker.publish(
        str(topic), {"type": "turn_finished", "turn_id": "already-running"}
    )
    await _until(lambda: runner.active_work_count() == 0)


@pytest.mark.anyio
async def test_image_only_message_can_merge_into_live_session():
    class MergeIntoLive(FakeChat):
        def __init__(self):
            super().__init__(None)
            self.merged: tuple | None = None

        async def work_policy(self, topic_id, agent_instance_id=None):
            raise AssertionError("a delivered image needs no new work item")

        async def merge_into_running_turn(self, *args, **kwargs):
            self.merged = args
            return True

    chat = MergeIntoLive()
    runner, broker = _runner()
    topic = uuid.uuid4()
    await broker.publish(
        str(topic), {"type": "turn_started", "turn_id": "already-running"}
    )
    attachment = {"path": "uploads/img-a.png", "mime": "image/png"}

    async with broker.subscribe(str(topic)) as queue:
        await runner.receive_message(
            chat,
            topic,
            author="u",
            content="",
            attachments=[attachment],
        )
        frames = [await queue.get()]
        await _until(lambda: chat.merged is not None)

    # No mark on this path either — see the note in the test above.
    assert [frame["type"] for frame in frames] == ["user_block"]
    assert chat.merged is not None
    block_ids = chat.merged[1]
    assert len(block_ids) == 1
    assert chat.merged[2:] == ("", "u", [attachment])

    await broker.publish(
        str(topic), {"type": "turn_finished", "turn_id": "already-running"}
    )
    await _until(lambda: runner.active_work_count() == 0)


@pytest.mark.anyio
async def test_only_a_message_to_the_agent_owes_the_running_turn_an_answer():
    """Both reach the running session; only the one addressed to the agent
    holds it to answering before anything else (`driven/runner.py`). The
    other was said to somebody else in the room."""

    class MergeIntoLive(FakeChat):
        def __init__(self):
            super().__init__(None)
            self.owed: dict[str, bool] = {}

        async def work_policy(self, topic_id, agent_instance_id=None):
            raise AssertionError("a delivered mid-turn message needs no new turn")

        async def merge_into_running_turn(self, topic, ids, content, *args, **kwargs):
            self.owed[content] = kwargs.get("owes_reply", True)
            return True

    chat = MergeIntoLive()
    runner, broker = _runner()
    topic = uuid.uuid4()
    await broker.publish(
        str(topic), {"type": "turn_started", "turn_id": "already-running"}
    )
    await runner.receive_message(chat, topic, author="u", content="<@cheese-seat> 停")
    await runner.receive_message(chat, topic, author="u", content="我先去吃饭")
    await _until(lambda: len(chat.owed) == 2)

    assert chat.owed == {"<@cheese-seat> 停": True, "我先去吃饭": False}
    await broker.publish(
        str(topic), {"type": "turn_finished", "turn_id": "already-running"}
    )
    await _until(lambda: runner.active_work_count() == 0)
