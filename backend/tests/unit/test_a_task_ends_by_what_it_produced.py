"""A task's AI teammate ends it by what the work produced.

Changes to project files go through the task's checkout and are filed for
review; a file or an address is filed for review with it attached; research,
an answer or an investigation ends with a conclusion. A task in a project with
no repository is never sent to a checkout, which it does not have.
"""

import pytest

from app.domain.agent.harness.opening import task_section
from app.domain.agent.harness.prompt import (
    became_task_prompt,
    task_opening_prompt,
)


def _said_to(has_checkout: bool) -> dict[str, str]:
    return {
        "every turn": task_section(owner="alice", has_checkout=has_checkout),
        "turned into a task": became_task_prompt(
            owner="alice", has_checkout=has_checkout
        ),
        "opened": task_opening_prompt(
            title="T", owner="alice", source="S", has_checkout=has_checkout
        ),
    }


@pytest.mark.parametrize("has_checkout", [True, False])
def test_every_turn_says_each_way_a_task_ends(has_checkout):
    every_turn = _said_to(has_checkout)["every turn"]
    # A file or an address is attached when filing for review.
    assert "deliver" in every_turn and "deliver_url" in every_turn
    # Work that leaves nothing to review ends with a conclusion.
    assert "cheese_task(action=close)" in every_turn


def test_a_task_with_a_repository_changes_files_in_its_checkout():
    assert "cheese worktree" in _said_to(True)["every turn"]


@pytest.mark.parametrize("moment", ["every turn", "turned into a task", "opened"])
def test_a_task_without_a_repository_is_not_sent_to_a_checkout(moment):
    said = _said_to(False)[moment]
    assert "cheese worktree" not in said
    assert "工作目录" not in said


@pytest.mark.parametrize("has_checkout", [True, False])
@pytest.mark.parametrize("moment", ["turned into a task", "opened"])
def test_the_start_of_a_task_does_not_presume_filing_for_review(moment, has_checkout):
    assert "提交审阅" not in _said_to(has_checkout)[moment]


def test_a_landed_step_of_a_task_without_a_repository_is_not_a_merge():
    """A task without a repository hands over files or addresses: when a step
    of it is accepted, it is not told of a merge or sent to a new branch."""
    from app.domain.review.task_landing import (
        task_landed_prompt,
        task_step_landed_while_working_prompt,
        task_summary_prompt,
    )

    for said in (
        task_landed_prompt(title="T", task_id="t", has_checkout=False),
        task_step_landed_while_working_prompt(
            title="T", task_id="t", has_checkout=False
        ),
        task_summary_prompt(title="T", has_checkout=False),
    ):
        assert "合并" not in said
        assert "cheese worktree" not in said
        assert "工作目录" not in said
