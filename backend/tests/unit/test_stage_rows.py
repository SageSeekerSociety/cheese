"""What happens to submission items and rubric lines when a task's stage list
changes — one rule for the publish, the API and 芝士's drafts, and the same
table as the page's (`frontend/src/lib/taskStages.spec.ts`): the last stages
removed keep everything, unstaged; a removed stage takes its rows with it; a
task that had no stages hands its rows to the first new stage."""

import pytest

from app.domain.task.stages import rows_after_stage_change

CASES = [
    ("last stages removed", ["a", "b"], [], ["a", "b", None], [None, None, None], 0),
    ("a stage removed", ["a", "b"], ["b"], ["a", "b"], ["b"], 1),
    ("no stage before", [], ["x", "y"], [None], ["x"], 0),
    ("renamed without its id", ["a"], ["n"], ["a"], [], 1),
    ("no stage named, after a reorder", ["a", "b"], ["b", "a"], [None], ["a"], 0),
]


@pytest.mark.parametrize(
    "old_ids,new_ids,refs,kept,removed",
    [case[1:] for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_rows_follow_their_stage_or_leave_with_it(
    old_ids, new_ids, refs, kept, removed
):
    rows = [{"prompt": str(i), "stage": ref} for i, ref in enumerate(refs)]
    settled = rows_after_stage_change(rows, old_ids, new_ids)
    assert [row["stage"] for row in settled.kept] == kept
    assert len(settled.removed) == removed
