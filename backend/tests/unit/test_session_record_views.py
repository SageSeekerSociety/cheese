"""The overview cannot discard tool arguments, results or subagent attribution."""

from app.domain.session_record.views import parts


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
