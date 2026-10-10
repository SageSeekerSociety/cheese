"""A call the platform refused reaches the room as the sentence it said.

Claude Code hands the model a refused or failed call's text inside a
``<tool_use_error>`` tag. A room showed that tag, around the content blocks
the executor's MCP server answered with serialized as JSON, to the person
asking: ``<tool_use_error>[{"type":"text","text":"..."}]</tool_use_error>``.
"""

from app.domain.agent.harness.claude_code.events import Assembler
from app.domain.agent.service import AgentStepFailed, AgentStepOutput

SAID = "This machine cannot give the channel an isolated environment."


def _failed(content: str) -> list:
    return Assembler({}, "session-1").accept(
        {
            "type": "user",
            "uuid": "rec_1",
            "session_id": "session-1",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": content,
                        "is_error": True,
                    }
                ],
            },
        }
    )


def test_the_room_is_shown_the_refusal_without_the_models_tag():
    events = _failed(f"<tool_use_error>{SAID}</tool_use_error>")

    assert [type(event) for event in events] == [AgentStepFailed, AgentStepOutput]
    assert events[0].text == SAID
    assert events[1].text == SAID


def test_a_failure_that_only_mentions_the_tag_is_left_as_it_is():
    said = "grep found <tool_use_error> in the log"

    events = _failed(said)

    assert events[1].text == said
