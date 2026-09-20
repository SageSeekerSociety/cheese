"""Completion replaces partial text; another thread cannot flush that text."""

from app.domain.agent.harness.codex.events import Assembler
from app.domain.agent.service import (
    AgentMessage,
    AgentResult,
    AgentSessionInfo,
    AgentSubagentStart,
    AgentSubagentStop,
    AgentToolUse,
)


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


def test_child_events_do_not_replace_root_session_or_finish_its_turn():
    assembler = Assembler()
    root = assembler.accept(
        {
            "method": "thread/started",
            "params": {"thread": {"id": "root"}},
        }
    )
    assert root == [AgentSessionInfo("root")]
    for child, parent in (("child", "root"), ("grandchild", "child")):
        start = assembler.accept(
            {
                "method": "thread/started",
                "params": {
                    "thread": {
                        "id": child,
                        "parentThreadId": parent,
                        "agentRole": "explorer",
                    }
                },
            }
        )
        assert start == [AgentSubagentStart(child, "explorer", parent)]
    assembler.accept(
        {
            "method": "item/agentMessage/delta",
            "params": {
                "threadId": "root",
                "itemId": "root-item",
                "delta": "root pending",
            },
        }
    )
    for child in ("child", "grandchild"):
        tool = assembler.accept(
            {
                "method": "item/started",
                "params": {
                    "threadId": child,
                    "item": {
                        "id": "tool",
                        "type": "dynamicToolCall",
                        "tool": "Read",
                        "arguments": {"file_path": "README.md"},
                    },
                },
            }
        )[0]
        assert isinstance(tool, AgentToolUse)
        assert (tool.agent_id, tool.agent_type) == (child, "explorer")
        message = assembler.accept(
            {
                "method": "item/completed",
                "params": {
                    "threadId": child,
                    "item": {
                        "id": "answer",
                        "type": "agentMessage",
                        "text": "found it",
                    },
                },
            }
        )[0]
        assert isinstance(message, AgentMessage)
        assert (message.agent_id, message.agent_type) == (child, "explorer")
        stopped = assembler.accept(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": child,
                    "turn": {
                        "id": "turn",
                        "status": "completed",
                        "items": [],
                    },
                },
            }
        )
        assert len(stopped) == 1
        assert isinstance(stopped[0], AgentSubagentStop)
        assert stopped[0].text == "found it"
        assert stopped[0].agent_id == child
    assert [message.text for message in assembler.give_up()] == ["root pending"]
