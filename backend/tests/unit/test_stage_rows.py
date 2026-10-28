"""What happens when a task's stage list changes — one rule for the publish,
the API and 芝士's drafts, and the same tables as the page's
(`frontend/src/lib/taskStages.spec.ts`).

Items and rubric lines: the last stages removed keep everything, unstaged; a
removed stage takes its rows with it; a task that had no stages hands its rows
to the first new stage. Hand-ins never move between stages: when the list goes
empty only the first old stage becomes the task's one stage, and removing a
stage that holds a hand-in is refused."""

import pytest

from app.domain.task.stages import refused_stage_change, rows_after_stage_change

CASES = [
    ("last stages removed", ["a", "b"], [], ["a", "b", None], [None, None, None], 0),
    ("a stage removed", ["a", "b"], ["b"], ["a", "b"], ["b"], 1),
    ("no stage before", [], ["x", "y"], [None], ["x"], 0),
    ("renamed without its id", ["a"], ["n"], ["a"], [], 1),
    ("no stage named, after a reorder", ["a", "b"], ["b", "a"], [None], ["a"], 0),
    ("several stages to none", ["a", "b", "c"], [], ["b", "c"], [None, None], 0),
]

#: (case, old ids, new ids, positions holding a hand-in, the stage refused)
HAND_INS = [
    ("several stages to none, a hand-in in the second", ["a", "b"], [], {1}, 1),
    ("several stages to none, a hand-in in the first only", ["a", "b"], [], {0}, None),
    ("several stages to none, no hand-ins", ["a", "b"], [], set(), None),
    ("a stage holding a hand-in removed", ["a", "b"], ["b"], {0}, 0),
    ("reordered", ["a", "b"], ["b", "a"], {0, 1}, None),
    ("no stage before", [], ["x"], {0}, None),
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


@pytest.mark.parametrize(
    "old_ids,new_ids,held,refused",
    [case[1:] for case in HAND_INS],
    ids=[case[0] for case in HAND_INS],
)
def test_hand_ins_never_move_and_their_stage_cannot_be_removed(
    old_ids, new_ids, held, refused
):
    assert refused_stage_change(old_ids, new_ids, held) == refused
