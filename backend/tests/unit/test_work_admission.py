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

from app.domain.agent.runtime import AgentWorkRunner, InProcessBroker
from tests.turn_log import a_topic


class FakeChat:
    """Controllable stand-in for ChatService: converse turns block until
    released, so tests can observe concurrency and queue order."""

    def __init__(self, policy: dict | None, session_factory=None):
        self.policy = policy
        # Where the runner opens each turn's interval — a real ChatService
        # carries the database, so a stand-in that runs turns carries it too.
        self.session_factory = session_factory
        self.system_events: list[str] = []
        self.system_event_meta: list[dict | None] = []
        self.running = 0
        self.max_running = 0
        self.converse_calls: list[dict] = []
        self.release = asyncio.Event()

    async def work_policy(self, topic_id: uuid.UUID) -> dict | None:
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

    def has_running_turn(self, topic_id: uuid.UUID) -> bool:
        return False

    async def thread_at(self, topic_id: uuid.UUID):
        """这些用例说的都是房间：一条活没有轮次可以被准入，也没有轮次可以排队。"""
        return None

    async def post_user_message(self, topic_id, **kwargs):
        self.converse_calls.append({"received": True, **kwargs})
        ids = []
        payloads = []
        if kwargs["content"]:
            block_id = uuid.uuid4()
            ids.append(block_id)
            payloads.append({"id": str(block_id), "content": kwargs["content"]})
        for attachment in kwargs.get("attachments") or []:
            block_id = uuid.uuid4()
            ids.append(block_id)
            payloads.append(
                {
                    "id": str(block_id),
                    "content": attachment["path"],
                    "kind": "attachment",
                }
            )
        return payloads, ids[0], ids, False

    async def merge_into_running_turn(self, *args):
        return None

    async def converse(self, **kwargs):
        self.converse_calls.append(kwargs)
        if not kwargs.get("summon", True):
            # summon=False = post-only pass (used to land a refused message).
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

        async def merge_into_running_turn(self, topic, ids, content, *args):
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
            await broker.receive_message(
                chat, topic, author="u", content="first", summon=False
            )
            await chat.entered.wait()
            assert (await browser.get())["block"]["content"] == "first"
            await broker.receive_message(
                chat, topic, author="u", content="second", summon=False
            )
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
        async def merge_into_running_turn(self, topic, ids, content, *args):
            if content == "first":
                raise RuntimeError("executor unavailable")
            self.converse_calls.append({"delivered": content})
            return True

    chat = FailedDelivery(None)
    runner, broker = _runner()
    topic = uuid.uuid4()
    async with broker.subscribe(str(topic)) as browser:
        for content in ("first", "second"):
            await broker.receive_message(
                chat, topic, author="u", content=content, summon=False
            )
        await runner.drain()
        assert {"delivered": "second"} in chat.converse_calls
        frames = []
        while not browser.empty():
            frames.append(browser.get_nowait())
        assert sum(frame["type"] == "user_block" for frame in frames) == 2
        assert sum(frame["type"] == "error" for frame in frames) == 1


@pytest.mark.anyio
async def test_waiting_recipient_does_not_block_current_agent_followup():
    class Recipients(FakeChat):
        def __init__(self):
            super().__init__(None)
            self.selected = "b"
            self.waiting = asyncio.Event()
            self.finish_a = asyncio.Event()
            self.delivered = []

        async def post_user_message(self, *args, **kwargs):
            payloads, anchor, ids, duplicate = await super().post_user_message(
                *args, **kwargs
            )
            for payload in payloads:
                payload["meta"] = {"agent_recipient": {"handle": self.selected}}
            return payloads, anchor, ids, duplicate

        async def wait_for_recipient(self, topic, recipient):
            if recipient == "b":
                self.waiting.set()
                await self.finish_a.wait()
                return True
            return False

        async def merge_into_running_turn(self, topic, ids, content, *args, **kwargs):
            self.delivered.append((kwargs["recipient_handle"], content))
            return True

        async def ack_summon(self, *args):
            return None

    chat = Recipients()
    runner, broker = _runner()
    # Constructing an unrelated work runner must not replace the subscriber.
    other = AgentWorkRunner(broker)
    topic = uuid.uuid4()
    try:
        await broker.receive_message(
            chat, topic, author="u", content="B's next task", summon=True
        )
        await chat.waiting.wait()
        chat.selected = "a"
        await broker.receive_message(
            chat, topic, author="u", content="Stop A's current task", summon=False
        )
        await _until(lambda: bool(chat.delivered))
        assert chat.delivered == [("a", "Stop A's current task")]
        assert other.active_work_count() == 0
        chat.finish_a.set()
        await runner.drain()
        assert chat.delivered[-1] == ("b", "B's next task")
    finally:
        chat.finish_a.set()
        await runner.drain()


async def _until(cond, timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not cond():
            await asyncio.sleep(0.01)


async def _frames_through(queue, final_type: str) -> list[dict]:
    frames = []
    async with asyncio.timeout(2.0):
        while True:
            frame = await queue.get()
            frames.append(frame)
            if frame["type"] == final_type:
                return frames


def _runner() -> tuple[AgentWorkRunner, InProcessBroker]:
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker, turn_timeout_s=5.0)
    runner.subscribe_messages()
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

    runner.submit(chat, topic, author="u1", content="第一轮", summon=True)
    await _until(lambda: chat.running == 1)

    # Second turn: gate is full → queued, with a visible system event (0 ahead).
    runner.submit(chat, topic, author="u2", content="第二轮", summon=True)
    await _until(lambda: len(chat.system_events) == 1)
    assert "排队" in chat.system_events[0]

    # Third turn: one waiter already ahead of it.
    runner.submit(chat, topic, author="u3", content="第三轮", summon=True)
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

    runner.submit(chat, await a_topic(db_factory), author="u", content="a", summon=True)
    runner.submit(chat, await a_topic(db_factory), author="u", content="b", summon=True)
    await _until(lambda: chat.running == 2)

    # Both run concurrently; no queue event was posted.
    assert chat.system_events == []

    chat.release.set()
    await _until(lambda: runner.active_work_count() == 0)
    assert chat.max_running == 2


@pytest.mark.anyio
async def test_exhausted_credits_refuses_turn_but_lands_message():
    chat = FakeChat(
        {
            "project_id": "proj-3",
            "max_concurrent_turns": 2,
            "credits_exhausted": True,
        }
    )
    runner, broker = _runner()
    topic = uuid.uuid4()

    frames = []
    async with broker.subscribe(str(topic)) as q:
        runner.submit(chat, topic, author="u1", content="还在吗", summon=True)
        async with asyncio.timeout(2.0):
            while True:
                frame = await q.get()
                frames.append(frame)
                if frame["type"] == "error":
                    break

    # The human's message landed (posting is free), via a summon=False pass.
    assert [f["type"] for f in frames] == ["user_block", "event_block", "error"]
    assert len(chat.converse_calls) == 1
    assert chat.converse_calls[0]["summon"] is False
    # The agent never ran.
    assert chat.max_running == 0
    # The refusal is the PLATFORM's structured copy, in the topic 现场.
    assert any("tokens 额度已用完" in e for e in chat.system_events)
    assert "tokens 额度已用完" in frames[-1]["message"]
    await _until(lambda: runner.active_work_count() == 0)


@pytest.mark.anyio
async def test_unknown_policy_admits_ungated(db_factory):
    chat = FakeChat(None, db_factory)  # topic unknown / unmetered deployment
    runner, _ = _runner()

    runner.submit(
        chat, await a_topic(db_factory), author="u", content="hi", summon=True
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
        await broker.receive_message(
            chat, topic, author="u", content="这条必须先落库", summon=True
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
    # One receive operation, no summon=False second pass and no model turn.
    assert len(chat.converse_calls) == 1
    assert chat.converse_calls[0]["received"] is True
    assert chat.converse_calls[0]["content"] == "这条必须先落库"
    assert "summon" not in chat.converse_calls[0]


@pytest.mark.anyio
async def test_unsummoned_message_never_touches_turn_admission():
    class PostOnly(FakeChat):
        async def work_policy(self, topic_id):
            raise AssertionError("plain messages do not enter the turn gate")

    chat = PostOnly(None)
    runner, broker = _runner()
    topic = uuid.uuid4()
    async with broker.subscribe(str(topic)) as queue:
        await broker.receive_message(
            chat, topic, author="u", content="只发消息", summon=False
        )
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
        await broker.receive_message(
            chat, topic, author="u", content="正常开工", summon=True
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
            await broker.receive_message(
                chat, topic, author="u", content="正常开工", summon=True
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
async def test_live_delivery_fallback_reports_error_then_runs_normally(
    db_factory,
    delivery_result,
):
    class FailedLiveDelivery(FakeChat):
        def has_running_turn(self, topic_id: uuid.UUID) -> bool:
            return True

        async def merge_into_running_turn(self, *args):
            return delivery_result

        async def converse_prepared(self, **kwargs):
            yield {"type": "done"}

    chat = FailedLiveDelivery(None, db_factory)
    runner, broker = _runner()
    topic = await a_topic(db_factory)

    async with broker.subscribe(str(topic)) as queue:
        await broker.receive_message(
            chat, topic, author="u", content="补充一条", summon=True
        )
        frames = await _frames_through(queue, "turn_finished")

    assert [frame["type"] for frame in frames] == [
        "user_block",
        "event_block",
        "turn_started",
        "done",
        "turn_finished",
    ]
    assert "没能送进正在进行的会话" in frames[1]["block"]["content"]
    assert frames[1]["block"]["meta"]["event_type"] == "delivery_fallback"
    assert frames[1]["block"]["meta"]["severity"] == "error"
    assert frames[1]["block"]["meta"]["who"] == "platform"
    assert len(chat.converse_calls) == 1
    await _until(lambda: runner.active_work_count() == 0)


@pytest.mark.anyio
async def test_receipted_mid_session_message_has_no_second_done():
    class MergeIntoLive(FakeChat):
        def __init__(self):
            super().__init__(None)
            self.merged: tuple | None = None

        async def work_policy(self, topic_id):
            raise AssertionError("a delivered mid-turn message needs no new turn")

        async def merge_into_running_turn(self, *args):
            self.merged = args
            return True

    chat = MergeIntoLive()
    runner, broker = _runner()
    topic = uuid.uuid4()
    await broker.publish(
        str(topic), {"type": "turn_started", "turn_id": "already-running"}
    )

    async with broker.subscribe(str(topic)) as queue:
        await broker.receive_message(
            chat, topic, author="u", content="补充一条", summon=True
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
    assert chat.merged[2:] == ("补充一条", "u", None)
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

        async def work_policy(self, topic_id):
            raise AssertionError("a delivered image needs no new work item")

        async def merge_into_running_turn(self, *args):
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
        await broker.receive_message(
            chat,
            topic,
            author="u",
            content="",
            attachments=[attachment],
            summon=True,
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
