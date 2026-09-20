"""The two list sorts are total orders — asserted on the SQL, not on the rows.

Which of two tied rows comes back first is the planner's choice on a given run,
so the row-level test next door
(`tests/integration/test_feedback.py::test_rows_that_tie_on_the_sort_key...`)
can pass against an ordering that has no tiebreak at all. The `ORDER BY` cannot
be that lucky: this is the same fact, read where it is decided rather than
where it happens to show.

`created_at` is the application clock, so two rows written in the same
millisecond compare equal; `display_no` is the sequence, so it never does.
`OFFSET` pagination over an ordering that rows can tie on repeats one row and
skips another, and the row that goes missing is the one the person was reading.
"""

from typing import cast

from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.feedback.repositories import FeedbackRepository

#: The last key of both sorts. A sort whose last key can tie is not an ordering.
LAST_KEY = "feedback.display_no DESC"


def _keys(sort: str) -> list[str]:
    repo = FeedbackRepository(cast(AsyncSession, None))
    stmt = repo._list_stmt(where=[], sort=sort, limit=20, offset=0)
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    tail = sql.split("ORDER BY", 1)[1].split("LIMIT", 1)[0]
    return [key.strip() for key in tail.split(",") if key.strip()]


def test_both_sorts_end_in_a_column_that_cannot_tie() -> None:
    """`new` and `hot` alike: the last thing compared is the row's number."""
    for sort in ("new", "supports"):
        assert _keys(sort)[-1] == LAST_KEY, sort


def test_the_hot_sort_still_counts_first() -> None:
    """The tiebreak is a last resort — it must not displace the support count."""
    keys = _keys("supports")
    assert keys[0].startswith("count("), keys
    assert keys[:2] == ["count(feedback_supports.id) DESC", "feedback.created_at DESC"]
