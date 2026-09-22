import asyncio
import uuid
from types import SimpleNamespace

from tests import conftest


def test_wait_work_idle_waits_for_hook_writes_but_not_an_idle_lifecycle(monkeypatch):
    """An active marker alone does not make teardown wait for the full timeout."""
    runner = SimpleNamespace(
        _tasks=set(),
        _broker=SimpleNamespace(
            _active={"room": {"turn"}},
        ),
    )
    pending = [{"room"}, set()]
    sleeps = []

    monkeypatch.setattr(conftest, "get_work_runner", lambda: runner)
    monkeypatch.setattr(
        conftest,
        "_topics_with_pending_hooks",
        lambda: pending[0],
    )

    def finish_hook_write(seconds):
        sleeps.append(seconds)
        pending.pop(0)

    monkeypatch.setattr(conftest.time, "sleep", finish_hook_write)

    conftest.wait_work_idle()

    assert sleeps == [0.01]


def test_pending_hook_topics_include_live_writes_and_exclude_dead_consumers(
    monkeypatch,
):
    from app.domain.agent.harness.claude_code import hooks_substrate

    topic_id = uuid.uuid4()
    queue = asyncio.Queue()
    queue.put_nowait({"hook_event_name": "Stop"})
    consuming = asyncio.Lock()
    live_loop = asyncio.new_event_loop()
    closed_loop = asyncio.new_event_loop()
    closed_loop.close()
    subscription = SimpleNamespace(
        sink=SimpleNamespace(queue=queue),
        consuming=consuming,
        consumer_task=SimpleNamespace(
            done=lambda: False,
            get_loop=lambda: live_loop,
        ),
    )
    runtime = object.__new__(hooks_substrate.ClaudeCodeRuntime)
    runtime._subscriptions = {topic_id: subscription}
    monkeypatch.setattr(hooks_substrate, "_RUNTIMES", [runtime])

    try:
        assert conftest._topics_with_pending_hooks() == {str(topic_id)}

        queue.get_nowait()
        queue.task_done()
        assert conftest._topics_with_pending_hooks() == set()

        live_loop.run_until_complete(consuming.acquire())
        assert conftest._topics_with_pending_hooks() == {str(topic_id)}
        consuming.release()

        subscription.consumer_task = SimpleNamespace(
            done=lambda: False,
            get_loop=lambda: closed_loop,
        )
        queue.put_nowait({"hook_event_name": "Stop"})
        assert conftest._topics_with_pending_hooks() == set()
    finally:
        live_loop.close()
