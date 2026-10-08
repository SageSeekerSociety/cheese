"""The rows the deadline sweep looks for, as the SQL that finds them.

`deadline_scheduler` asks every 900 seconds who is past their deadline with
nothing in hand, and `ix_task_membership_deadline` is built on exactly this
predicate. PostgreSQL only takes a partial index when the query's WHERE implies
the index's WHERE, and it proves that by comparing expressions, not meanings —
so the bound form `completion_status IN ($1, $2)` cannot be proved to imply a
predicate that holds the two statuses as constants, and the index is passed by.
A statement the driver keeps prepared is planned generically after a few runs,
and a generic plan cannot see what those parameters hold either. Measured on a
200,000-row `task_membership` with 357 rows matching the sweep, under
`force_generic_plan` (2026-10-07): a Seq Scan with the bound form, a scan of
`ix_task_membership_deadline` with this literal one. So this is literal SQL,
written once, and both sides use it.

The migration that builds the index spells the same text out again, because a
migration is a snapshot and must not change when this file does;
`tests/integration/test_task_indexes.py` fails when the two disagree.

The two statuses are `submission_state`'s and the reasoning for them is the
sweep's; they are literals here because an index predicate is evaluated per row
and cannot hold a parameter. `tests/unit/test_task_indexed_rows.py` pins them
to those constants, so this file cannot drift away from the axis it serves.

Every fragment names columns of `task_membership` without a table prefix (an
index predicate cannot carry one), so a query using it must read that table
alone.
"""

from sqlalchemy import text

#: A membership that was not deleted. Every reader in this domain filters it,
#: and it is the first half of every partial index on `task_membership`.
LIVE_ROWS = text("deleted_at IS NULL")


def _one_of(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


#: Past its deadline with nothing handed in: never submitted, or sent back and
#: not resubmitted. `PENDING_REVIEW` is deliberately NOT here — a deadline is
#: the last moment to hand work in, and someone in that state handed it in;
#: what has not happened since is the review, and failing them makes the
#: platform punish a person for a queue they do not control. `SUCCESS` is not
#: here either — someone whose work passed is past this question.
#:
#: The sweep still also keeps its `~has_work_in_hand` guard: the two statuses
#: are the ones a membership with nothing in hand can carry, so the two say the
#: same thing, and the conjunction is kept because a wrong write fails a person
#: for work they did while a guard that only skips is the cheaper mistake.
SWEEPABLE_STATUSES = ("REJECTED_RESUBMITTABLE", "NOT_SUBMITTED")

SWEEPABLE_ROWS = text(
    f"deleted_at IS NULL AND completion_status IN ({_one_of(SWEEPABLE_STATUSES)})"
)
