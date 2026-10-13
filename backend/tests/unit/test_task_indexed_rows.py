"""The sweep's two statuses are `submission_state`'s, spelled as literals.

`app.domain.task.indexed_rows` cannot import them: `submission_state` reads the
models, the models declare the index, and the index is built on this module's
text. So the values are written out there, and this is what keeps them the ones
the axis derives — the same two a membership with nothing in hand can carry.
"""

from app.domain.task.indexed_rows import LIVE_ROWS, SWEEPABLE_ROWS, SWEEPABLE_STATUSES
from app.domain.task.submission_state import (
    COMPLETION_STATUS_FAILED,
    COMPLETION_STATUS_NOT_SUBMITTED,
    COMPLETION_STATUS_PENDING_REVIEW,
    COMPLETION_STATUS_REJECTED_RESUBMITTABLE,
    COMPLETION_STATUS_SUCCESS,
)


def test_the_sweepable_statuses_are_the_ones_with_nothing_in_hand() -> None:
    assert set(SWEEPABLE_STATUSES) == {
        COMPLETION_STATUS_REJECTED_RESUBMITTABLE,
        COMPLETION_STATUS_NOT_SUBMITTED,
    }


def test_the_statuses_that_handed_work_in_are_not_sweepable() -> None:
    """`PENDING_REVIEW` is someone waiting on the queue, not someone who never
    handed anything in, and `SUCCESS` is past the question; `FAILED` is what the
    sweep writes, not what it looks for."""
    for status in (
        COMPLETION_STATUS_PENDING_REVIEW,
        COMPLETION_STATUS_SUCCESS,
        COMPLETION_STATUS_FAILED,
    ):
        assert status not in SWEEPABLE_STATUSES


def test_the_row_predicate_is_the_two_statuses_in_live_rows() -> None:
    """What the index is built on: the same text, not a paraphrase of it."""
    statuses = ", ".join(f"'{status}'" for status in SWEEPABLE_STATUSES)
    assert LIVE_ROWS.text == "deleted_at IS NULL"
    assert SWEEPABLE_ROWS.text == (
        f"deleted_at IS NULL AND completion_status IN ({statuses})"
    )
