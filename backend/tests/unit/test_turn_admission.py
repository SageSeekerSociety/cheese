"""TurnRunner admission: project concurrency gate + credit exhaustion (§9.1).

Functional tests against a fake ChatService: the runner must (a) run at most
max_concurrent_turns turns per project at once, queueing the rest FIFO with a
visible "排队中" system event, and (b) refuse a turn outright when the
project's compute credits are exhausted — landing the human's message but
posting the platform's exhaustion event instead of running the agent.
"""

import asyncio
import uuid

import pytest

from app.domain.agent.runtime import InProcessBroker, TurnRunner


class FakeChat:
    """Controllable stand-in for ChatService: converse turns block until
    released, so tests can observe concurrency and queue order."""

    def __init__(self, policy: dict | None):
        self.policy = policy
        self.system_events: list[str] = []
        self.running = 0
        self.max_running = 0
        self.converse_calls: list[dict] = []
        self.release = asyncio.Event()

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


def _runner() -> tuple[TurnRunner, InProcessBroker]:
    broker = InProcessBroker()
    return TurnRunner(broker, turn_timeout_s=5.0), broker


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
    await _until(lambda: runner.active_turns() == 0)
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
    await _until(lambda: runner.active_turns() == 0)
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
    await _until(lambda: runner.active_turns() == 0)


@pytest.mark.anyio
async def test_unknown_policy_admits_ungated():
    chat = FakeChat(None)  # topic unknown / unmetered deployment
    runner, _ = _runner()

    runner.submit(chat, uuid.uuid4(), author="u", content="hi", summon=True)
    await _until(lambda: chat.running == 1)
    chat.release.set()
    await _until(lambda: runner.active_turns() == 0)
    assert len(chat.converse_calls) == 1
