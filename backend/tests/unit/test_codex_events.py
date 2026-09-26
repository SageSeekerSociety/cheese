"""Completion replaces partial text; another thread cannot flush that text."""

from app.domain.agent.harness.codex.events import Assembler
from app.domain.agent.service import (
    AgentMessage,
    AgentResult,
    AgentRetrying,
    AgentSessionInfo,
    AgentStepFailed,
    AgentStepOutput,
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
        assert tool.thread_label == "explorer"
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
        assert message.thread_label == "explorer"
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


def _error(message: str, *, will_retry: bool) -> dict:
    return {
        "method": "error",
        "params": {
            "threadId": "thread",
            "turnId": "turn",
            "willRetry": will_retry,
            "error": {"message": message},
        },
    }


def test_a_retry_without_a_count_is_still_a_retry():
    """「Reconnecting... waiting for network」 says no attempt number, and the
    room still has to hear that the turn is retrying rather than thinking."""
    [event] = Assembler().accept(
        _error("Reconnecting... waiting for network", will_retry=True)
    )
    assert isinstance(event, AgentRetrying)
    assert event.attempt is None
    assert event.error == "Reconnecting... waiting for network"


def test_an_error_that_will_not_be_retried_is_left_to_the_turn():
    """The failure that ends the turn arrives with ``turn/completed``; saying
    「retrying」 for it would promise a retry that never comes."""
    assert Assembler().accept(_error("stream disconnected", will_retry=False)) == []


def _tool_completed(**item) -> dict:
    return {
        "method": "item/completed",
        "params": {
            "threadId": "thread",
            "item": {
                "type": "dynamicToolCall",
                "id": "call-1",
                "tool": "bash",
                "arguments": {"command": "make"},
                "contentItems": [{"type": "inputText", "text": "make: *** Error 2"}],
                **item,
            },
        },
    }


def test_a_tool_call_the_platform_answered_as_failed_marks_its_step():
    """The platform's own tool bridge answers a failed call with
    ``success: false``; that step turns red like any harness's failed step."""
    events = Assembler().accept(_tool_completed(status="completed", success=False))

    assert [type(e) for e in events] == [AgentStepFailed, AgentStepOutput]
    assert events[0].call_id == "call-1"
    assert events[0].text == "make: *** Error 2"


def test_an_item_codex_itself_reports_failed_marks_its_step():
    events = Assembler().accept(_tool_completed(status="failed", success=None))

    assert isinstance(events[0], AgentStepFailed)


def test_a_tool_call_that_worked_is_not_marked_failed():
    events = Assembler().accept(_tool_completed(status="completed", success=True))

    assert [type(e) for e in events] == [AgentStepOutput]
