"""The overview cannot discard tool arguments, results or subagent attribution."""

from app.domain.session_record.views import lines, parts


def test_complete_tool_arguments_and_outputs_remain_expandable():
    argument = "a" * 5000
    output = "HEAD\n" + "line\n" * 3000 + "END"
    record = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "call",
                    "name": "Bash",
                    "input": {"command": argument, "other": 7},
                }
            ]
        },
    }
    call = parts(record)[0]
    assert call["input"] == {"command": argument, "other": 7}
    result = parts(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call",
                        "is_error": True,
                        "content": output,
                    }
                ]
            },
        }
    )[0]
    assert result["text"] == output
    assert result["failed"] is True
    assert result["call_id"] == "call"


def test_thinking_does_not_turn_a_signature_into_prose():
    result = parts(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "Consider both cases",
                        "signature": "secret-signature",
                    },
                    {"type": "redacted_thinking", "data": "encrypted"},
                ]
            },
        }
    )
    assert result[0]["text"] == "Consider both cases"
    assert result[1]["text"] == ""
    assert "encrypted" not in str(result)
    assert "secret-signature" not in str(result)


def test_an_unknown_record_is_still_an_inspectable_record():
    assert parts({"method": "future/event", "params": {"value": "new"}}) == []


def _codex(method: str, item: dict) -> dict:
    return {"method": method, "params": {"threadId": "thread-main", "item": item}}


def test_codex_notifications_read_as_the_same_parts():
    """Codex's completed item carries the call and its return together, so one
    record says what was run and what came back — the parts 现场 renders."""
    finished = parts(
        _codex(
            "item/completed",
            {
                "type": "dynamicToolCall",
                "id": "call-1",
                "tool": "bash",
                "arguments": {"command": "make test"},
                "success": False,
                "contentItems": [{"type": "inputText", "text": "boom"}],
            },
        )
    )
    assert finished[0]["kind"] == "tool"
    assert finished[0]["call_id"] == "call-1"
    assert finished[0]["input"] == {"command": "make test"}
    assert finished[1] == {
        "kind": "output",
        "call_id": "call-1",
        "text": "boom",
        "failed": True,
    }

    # The started half is a partial stream of the same step; it is not read
    # again, or the step would appear twice in a timeline and a file.
    assert (
        parts(
            _codex(
                "item/started",
                {
                    "type": "dynamicToolCall",
                    "id": "call-1",
                    "tool": "bash",
                    "arguments": {"command": "make test"},
                },
            )
        )
        == []
    )

    said = parts(
        _codex(
            "item/completed",
            {"type": "agentMessage", "id": "m1", "text": "done"},
        )
    )
    assert said == [{"kind": "text", "text": "done"}]

    thought = parts(
        _codex(
            "item/completed",
            {"type": "reasoning", "id": "r1", "summary": ["weigh both"], "content": []},
        )
    )
    assert thought == [{"kind": "thinking", "text": "weigh both"}]


def test_pi_entries_read_as_the_same_parts():
    call = parts(
        {
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "working"},
                    {
                        "type": "toolCall",
                        "id": "c1",
                        "name": "bash",
                        "arguments": {"command": "ls"},
                    },
                ],
            },
        }
    )
    assert [part["kind"] for part in call] == ["text", "tool"]
    assert call[1]["call_id"] == "c1"
    assert call[1]["input"] == {"command": "ls"}

    output = parts(
        {
            "type": "message",
            "message": {
                "role": "toolResult",
                "toolCallId": "c1",
                "isError": True,
                "content": [{"type": "text", "text": "no such file"}],
            },
        }
    )
    assert output[0] == {
        "kind": "output",
        "call_id": "c1",
        "text": "no such file",
        "failed": True,
    }


def test_one_line_shape_for_all_three_harness_records():
    """A transcript row says the same thing about the same moment whichever
    harness journaled it — that is what makes one file greppable."""
    records = [
        (
            "claude-code",
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "c",
                            "name": "Bash",
                            "input": {"command": "make"},
                        }
                    ],
                },
            },
        ),
        (
            "codex",
            _codex(
                "item/completed",
                {
                    "type": "dynamicToolCall",
                    "id": "c",
                    "tool": "Bash",
                    "arguments": {"command": "make"},
                    "success": True,
                },
            ),
        ),
        (
            "pi",
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "toolCall",
                            "id": "c",
                            "name": "Bash",
                            "arguments": {"command": "make"},
                        }
                    ],
                },
            },
        ),
    ]
    rows = lines(
        [
            {
                "sequence": number,
                "at": "2026-10-22T08:00:00+00:00",
                "turn_id": "turn",
                "agent_handle": "cheese",
                "harness": harness,
                "record": record,
            }
            for number, (harness, record) in enumerate(records)
        ]
    )
    assert [set(row) for row in rows] == [set(rows[0])] * 3
    assert [row["conversation"] for row in rows] == [None, None, None]
    assert [row["subagent"] for row in rows] == [False, False, False]
    assert [row["parts"][0]["kind"] for row in rows] == ["tool", "tool", "tool"]
    assert [row["parts"][0]["input"] for row in rows] == [{"command": "make"}] * 3
    assert [row["role"] for row in rows] == ["assistant", None, "assistant"]


def test_a_subagents_own_records_are_marked_whichever_harness_wrote_them():
    """A reader keeps the spawner's steps apart from what the spawned thing
    did. Claude Code and pi mark each record; Codex marks the thread, once, in
    the record that started it."""
    items = [
        {"record": {"type": "assistant", "message": {"content": []}}},
        {"record": {"type": "assistant", "parent_tool_use_id": "toolu_task"}},
        {"record": {"type": "message", "subagent": {"id": "pi-child"}}},
        {"record": {"method": "thread/started", "params": {"thread": {"id": "child"}}}},
        {
            "record": {
                "method": "thread/started",
                "params": {"thread": {"id": "sub", "parentThreadId": "child"}},
            }
        },
        {"record": {"method": "item/completed", "params": {"threadId": "sub"}}},
        {"record": {"method": "item/completed", "params": {"threadId": "main"}}},
    ]
    for number, item in enumerate(items):
        item.update(
            sequence=number,
            at="2026-10-22T08:00:00+00:00",
            turn_id="turn",
            agent_handle="cheese",
            harness="codex",
        )
    assert [row["subagent"] for row in lines(items)] == [
        False,
        True,
        True,
        # The record that starts a child thread is itself of that thread, so it
        # is the child's row too.
        False,
        True,
        True,
        False,
    ]
