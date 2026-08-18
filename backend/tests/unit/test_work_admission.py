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


class FakeChat:
    """Controllable stand-in for ChatService: converse turns block until
    released, so tests can observe concurrency and queue order."""

    def __init__(self, policy: dict | None):
        self.policy = policy
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
        return payloads, ids[0], ids

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
    return AgentWorkRunner(broker, turn_timeout_s=5.0), broker


@pytest.mark.anyio
async def test_concurrency_gate_queues_and_announces_position():
    chat = FakeChat(
        {
            "project_id": "proj-1",
            "max_concurrent_turns": 1,
            "credits_exhausted": False,
        }
    )
    runner, _ = _runner()
    topic = uuid.uuid4()

    runner.submit(chat, topic, author="u1", content="第一轮", summon=True)
    await _until(lambda: chat.running == 1)

    # Second turn: gate is full → queued, with a visible system event (0 ahead).
    runner.submit(chat, topic, author="u2", content="第二轮", summon=True)
    await _until(lambda: len(chat.system_events) == 1)
    assert "排队" in chat.system_events[0]
    assert "等前面的轮次结束" in chat.system_events[0]

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
async def test_concurrency_gate_allows_up_to_limit_without_queueing():
    chat = FakeChat(
        {
            "project_id": "proj-2",
            "max_concurrent_turns": 2,
            "credits_exhausted": False,
        }
    )
    runner, _ = _runner()

    runner.submit(chat, uuid.uuid4(), author="u", content="a", summon=True)
    runner.submit(chat, uuid.uuid4(), author="u", content="b", summon=True)
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
    assert any("算力额度已用完" in e for e in chat.system_events)
    assert "算力额度已用完" in frames[-1]["message"]
    await _until(lambda: runner.active_work_count() == 0)


@pytest.mark.anyio
async def test_unknown_policy_admits_ungated():
    chat = FakeChat(None)  # topic unknown / unmetered deployment
    runner, _ = _runner()

    runner.submit(chat, uuid.uuid4(), author="u", content="hi", summon=True)
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
        await runner.submit_message(
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
        await runner.submit_message(
            chat, topic, author="u", content="只发消息", summon=False
        )
        assert (await queue.get())["type"] == "user_block"
        assert (await queue.get())["type"] == "done"
    assert runner.active_work_count() == 0


@pytest.mark.anyio
async def test_normal_message_without_live_work_queues_without_fallback_error():
    class Prepared(FakeChat):
        async def converse_prepared(self, **kwargs):
            yield {"type": "done"}

    chat = Prepared(None)
    runner, broker = _runner()
    topic = uuid.uuid4()

    async with broker.subscribe(str(topic)) as queue:
        await runner.submit_message(
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
@pytest.mark.parametrize("delivery_result", [False, None])
async def test_live_delivery_fallback_reports_error_then_runs_normally(
    delivery_result,
):
    class FailedLiveDelivery(FakeChat):
        def has_running_turn(self, topic_id: uuid.UUID) -> bool:
            return True

        async def merge_into_running_turn(self, *args):
            return delivery_result

        async def converse_prepared(self, **kwargs):
            yield {"type": "done"}

    chat = FailedLiveDelivery(None)
    runner, broker = _runner()
    topic = uuid.uuid4()

    async with broker.subscribe(str(topic)) as queue:
        await runner.submit_message(
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
    assert "没能直接送进正在进行的会话" in frames[1]["block"]["content"]
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

        async def ack_summon(self, block_id, topic_id):
            return {"block_id": str(block_id), "reactions": []}

    chat = MergeIntoLive()
    runner, broker = _runner()
    topic = uuid.uuid4()
    await broker.publish(
        str(topic), {"type": "turn_started", "turn_id": "already-running"}
    )

    async with broker.subscribe(str(topic)) as queue:
        await runner.submit_message(
            chat, topic, author="u", content="补充一条", summon=True
        )
        frames = []
        async with asyncio.timeout(2):
            while len(frames) < 2:
                frames.append(await queue.get())

    assert [frame["type"] for frame in frames] == ["user_block", "reaction"]
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

        async def ack_summon(self, block_id, topic_id):
            return {"block_id": str(block_id), "reactions": []}

    chat = MergeIntoLive()
    runner, broker = _runner()
    topic = uuid.uuid4()
    await broker.publish(
        str(topic), {"type": "turn_started", "turn_id": "already-running"}
    )
    attachment = {"path": "uploads/img-a.png", "mime": "image/png"}

    async with broker.subscribe(str(topic)) as queue:
        await runner.submit_message(
            chat,
            topic,
            author="u",
            content="",
            attachments=[attachment],
            summon=True,
        )
        frames = [await queue.get(), await queue.get()]

    assert [frame["type"] for frame in frames] == ["user_block", "reaction"]
    assert chat.merged is not None
    block_ids = chat.merged[1]
    assert len(block_ids) == 1
    assert chat.merged[2:] == ("", "u", [attachment])

    await broker.publish(
        str(topic), {"type": "turn_finished", "turn_id": "already-running"}
    )
    await _until(lambda: runner.active_work_count() == 0)
