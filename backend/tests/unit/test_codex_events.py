"""Completion replaces partial text; another thread cannot flush that text."""

from app.domain.agent.harness.codex.events import Assembler
from app.domain.agent.service import AgentMessage, AgentResult


def test_completion_replaces_deltas_and_retains_message_identity():
    assembler = Assembler()
    assembler.accept(
        {
            "method": "item/started",
            "params": {
                "threadId": "thread",
                "startedAtMs": 1000,
                "item": {"type": "agentMessage", "id": "item", "text": ""},
            },
        }
    )
    assembler.accept(
        {
            "method": "item/agentMessage/delta",
            "params": {
                "threadId": "thread",
                "itemId": "item",
                "delta": "part",
            },
        }
    )
    complete = {
        "method": "item/completed",
        "params": {
            "threadId": "thread",
            "item": {
                "type": "agentMessage",
                "id": "item",
                "text": "whole reply",
            },
        },
    }
    event = assembler.accept(complete)[0]
    assert isinstance(event, AgentMessage)
    assert event.text == "whole reply"
    assert event.at.timestamp() == 1
    assert assembler.accept(complete)[0].eid == event.eid
    assert assembler.give_up() == []


def test_failed_thread_preserves_its_partial_reply_without_flushing_another():
    assembler = Assembler()
    for thread in ("first", "second"):
        assembler.accept(
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": thread,
                    "itemId": "item",
                    "delta": thread,
                },
            }
        )
    events = assembler.accept(
        {
            "method": "turn/completed",
            "params": {
                "threadId": "first",
                "turn": {
                    "id": "turn",
                    "status": "failed",
                    "items": [],
                    "error": {"message": "provider disconnected"},
                },
            },
        }
    )
    assert [event.text for event in events] == ["first", "provider disconnected"]
    assert isinstance(events[-1], AgentResult)
    assert events[-1].is_error
    assert [message.text for message in assembler.give_up()] == ["second"]
