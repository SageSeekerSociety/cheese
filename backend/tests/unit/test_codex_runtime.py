"""Room delivery survives a reader replacement without sending another turn."""

import uuid
from unittest.mock import AsyncMock

import pytest

from app.domain.agent.harness import AgentRuntime, Opening, SessionRef
from app.domain.agent.harness.codex.journal import Journal
from app.domain.agent.harness.codex.runtime import CodexRuntime, Handle
from app.domain.agent.service import AgentMessage, AgentResult


@pytest.mark.anyio
async def test_room_send_steer_and_reconnect_keep_one_work_owner(tmp_path):
    session = SessionRef(uuid.uuid4(), uuid.uuid4())
    work = uuid.uuid4()
    handle = Handle(session, "center", "/state", "thread", "agent", tmp_path / "mirror")
    journal = Journal(tmp_path / "remote")
    inputs = []
    active = False

    async def call(handle, method, params):
        nonlocal active
        if method == "events":
            return {"events": journal.read(params["after"])}
        if method == "ping":
            return {"turn_id": "turn" if active else None}
        if method == "interrupt":
            active = False
            return {"interrupted": True}
        assert method == "send"
        inputs.append(params)
        active = True
        journal.append(
            {
                "method": "turn/started",
                "params": {"threadId": "thread", "turn": {"id": "turn"}},
                "cheese": {"work_id": params["work_id"]},
            }
        )
        return {"turn_id": "turn"}

    channel = AsyncMock()
    channel.ensure.return_value = handle
    channel.images.return_value = ["data:image/png;base64,fixture"]
    channel.call.side_effect = call
    channel.discover.return_value = [handle]
    runtime = CodexRuntime(channel)
    assert isinstance(runtime, AgentRuntime)
    consumer = AsyncMock()
    receipts = AsyncMock()
    runtime.bind_events(consumer)
    runtime.bind_receipts(receipts)
    marks = []
    replacement = None
    try:
        assert await runtime.send(
            session,
            "first",
            Opening("system"),
            work_id=work,
            on_mark=marks.append,
            images=[{"path": "uploads/image.png"}],
        )
        assert marks == [work]
        assert inputs[0]["input_id"] == str(work)
        assert inputs[0]["images"] == ["data:image/png;base64,fixture"]
        assert await runtime.deliver(session.topic_id, "steer")
        assert inputs[1]["work_id"] == str(work)
        assert inputs[1]["input_id"] != inputs[0]["input_id"]
        assert [entry.args[1] for entry in receipts.await_args_list] == [
            "first",
            "steer",
        ]
        # Lose only the backend reader. The remote process completes on its own.
        await runtime._detach(session.topic_id)
        for method, params in [
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
        ]:
            journal.append(
                {
                    "method": method,
                    "params": params,
                    "cheese": {"work_id": str(work), "agent_handle": "agent"},
                }
            )
        replacement = CodexRuntime(channel)
        replacement.bind_events(consumer)
        assert await replacement.recover() == [session]
        assert not replacement.tasks
        await replacement.replay(session, known_texts=set())
        await replacement.replay(session, known_texts={"answer"})
        events = [entry.args[3] for entry in consumer.await_args_list]
        assert len([event for event in events if isinstance(event, AgentMessage)]) == 1
        assert len([event for event in events if isinstance(event, AgentResult)]) == 1
        assert len(inputs) == 2
        assert not replacement.work
        assert await replacement.interrupt(session)
    finally:
        await runtime._detach(session.topic_id)
        if replacement:
            await replacement._detach(session.topic_id)
        journal.close()
