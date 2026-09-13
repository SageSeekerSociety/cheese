"""Persistence failures replay stable event IDs before advancing the cursor."""

import uuid
from unittest.mock import AsyncMock

import pytest

from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.codex.backlog import CodexBacklog
from app.domain.agent.harness.codex.subscription import Subscription


@pytest.mark.anyio
async def test_failed_persistence_replays_reply_without_restarting_model_work(tmp_path):
    work = str(uuid.uuid4())
    rows = [
        {
            "sequence": i,
            "at": "2026-09-12T00:00:00+00:00",
            "record": {
                "method": method,
                "params": params,
                "cheese": {"work_id": work},
            },
        }
        for i, (method, params) in enumerate(
            [
                ("turn/started", {"threadId": "thread", "turn": {"id": "turn"}}),
                (
                    "item/completed",
                    {
                        "threadId": "thread",
                        "item": {
                            "id": "reply",
                            "type": "agentMessage",
                            "text": "answer",
                        },
                    },
                ),
                (
                    "turn/completed",
                    {
                        "threadId": "thread",
                        "turn": {
                            "id": "turn",
                            "status": "completed",
                            "items": [],
                        },
                    },
                ),
            ],
            1,
        )
    ]
    call = AsyncMock(return_value={"events": rows})
    consumer = AsyncMock(side_effect=RuntimeError("database disconnected"))
    activity = AsyncMock()
    session = SessionRef(uuid.uuid4(), uuid.uuid4())
    path = tmp_path / "events.sqlite"
    first = Subscription(session, path, call, consumer, activity)
    with pytest.raises(RuntimeError, match="database disconnected"):
        await first.drain()
    assert len(CodexBacklog(path).unread()) == 2
    failed_id = consumer.await_args.args[4]
    consumer = AsyncMock()
    call.return_value = {"events": []}
    second = Subscription(session, path, call, consumer, activity)
    assert await second.drain() == 2
    assert consumer.await_args_list[0].args[4] == failed_id
    assert consumer.await_args_list[1].args[5] is True
    assert not CodexBacklog(path).unread()
    assert [entry.args[0] for entry in call.await_args_list] == ["events", "events"]
    assert [entry.args[-1] for entry in activity.await_args_list] == [True, False]
