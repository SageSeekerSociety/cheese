"""A task's own session is told to keep the task's card under every harness
(Claude Code, Codex, pi); a channel's session is not, since it has no card to
keep. A real turn in each:
`tests/integration/test_a_task_turn_is_written_for_its_owner.py`.
"""

import pytest

from app.domain.agent.harness import CLAUDE_CODE, CODEX, PI
from app.domain.agent.room.system_prompt import session_system_prompt


def _prompt(*, harness: str, in_task: bool) -> str:
    return session_system_prompt(
        "你是芝士。",
        "",
        needs_place=True,
        has_doc=True,
        role=None,
        harness=harness,
        name="芝士",
        in_task=in_task,
    )


@pytest.mark.parametrize("harness", [CLAUDE_CODE, CODEX, PI])
def test_a_task_session_is_told_to_keep_the_card(harness):
    assert "cheese_map_card" in _prompt(harness=harness, in_task=True)


@pytest.mark.parametrize("harness", [CLAUDE_CODE, CODEX, PI])
def test_a_channel_session_is_not(harness):
    assert "cheese_map_card" not in _prompt(harness=harness, in_task=False)
