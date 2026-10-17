"""A task's agent is sent to `cheese_ask` whenever it needs its owner to decide.

The system prompt says that asking a person goes only through `cheese_ask`: the
question shows up with quick replies and the answer starts the next turn. The
instruction a task's session receives when the task is opened from a discussion
must not send it the other way. One that said to settle the open details with
`chat_send` was obeyed over the system prompt, and the owner got a plain
message where a question belonged.
"""

import pytest

from app.domain.agent.harness.prompt import task_opening_prompt


@pytest.mark.parametrize(
    "instruction",
    [
        task_opening_prompt(title="T", owner="alice", source="S"),
        task_opening_prompt(title="T", owner="alice", source="S", reviewer="bob"),
    ],
    ids=["opened", "opened-reviewed-by-another"],
)
def test_a_decision_for_the_owner_goes_through_cheese_ask(instruction):
    assert "cheese_ask" in instruction
    assert "chat_send" not in instruction
