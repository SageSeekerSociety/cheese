"""Work that waits for the session to commit does not run for a savepoint.

The lost race this pins (PR #3511, ``backend / integration (3/5)`` at 8464a1c8)::

    POST /topics/<id>/close   200   ← the close is staged, not committed yet
    "close a closed task's PR"      reads the task: still open
    → the PR stays open, and nothing retries.

SQLAlchemy dispatches ``SessionEvents.after_commit`` from
``SessionTransaction.commit`` whenever the transaction being committed has no
parent *or* is a savepoint, and releasing a savepoint commits nothing: another
connection still reads the rows as they were. Something in the request released
one — ``review.task_landing.tell_origin`` wraps the line it says in a savepoint
so that a courtesy line cannot take a landing back with it — so the post-close
jobs started a request-frame early, read the task as open on their own
connection, and gave up. Measured at 8464a1c8: the hook fired 70 ms before the
commit landed. ``test_a_closed_task_closes_its_pr`` raced it and lost every
run on this machine, while the parent commit — whose CI never ran the backend
layer, being a docs-only push — lost the same race in 3 of 6 runs.

``after_own_commit`` is the one place that waits for the commit that ends the
session's transaction instead. What it must not do is *skip* a commit, or run
the work twice. The four cases pin that contract: a released savepoint runs
nothing and the transaction's commit answers; that commit runs the work once; a
hook asked to wait for every commit runs for each one; a rollback spends
nothing, so the next commit still answers.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import after_own_commit


def _closes_one(db_session: AsyncSession, _portal) -> list[str]:
    """Register the hook, release a savepoint, then commit. Answers what ran."""
    fired: list[str] = []

    async def scenario() -> list[str]:
        after_own_commit(db_session, lambda: fired.append("committed"))
        async with db_session.begin_nested():
            pass  # released: after_commit fires, and nothing was committed
        assert fired == [], "a released savepoint is not a commit"
        await db_session.commit()
        return list(fired)

    return _portal.call(scenario)


def test_a_released_savepoint_is_not_the_session_committing(db_session, _portal):
    assert _closes_one(db_session, _portal) == ["committed"]


def test_the_commit_that_ends_the_transaction_runs_it_once(db_session, _portal):
    fired: list[str] = []

    async def scenario() -> list[str]:
        after_own_commit(db_session, lambda: fired.append("committed"))
        await db_session.commit()
        await db_session.commit()
        return list(fired)

    assert _portal.call(scenario) == ["committed"]


def test_work_that_waits_for_every_commit_runs_for_each_one(db_session, _portal):
    fired: list[str] = []

    async def scenario() -> list[str]:
        after_own_commit(db_session, lambda: fired.append("committed"), once=False)
        async with db_session.begin_nested():
            pass
        assert fired == []
        await db_session.commit()
        await db_session.commit()
        return list(fired)

    assert _portal.call(scenario) == ["committed", "committed"]


def test_a_rollback_runs_nothing_and_the_next_commit_answers(db_session, _portal):
    # a close that is rolled back and tried again must still do its work, so the
    # hook waits out the rollback rather than being spent on it
    fired: list[str] = []

    async def scenario() -> list[str]:
        after_own_commit(db_session, lambda: fired.append("committed"))
        await db_session.rollback()
        assert fired == []
        await db_session.commit()
        return list(fired)

    assert _portal.call(scenario) == ["committed"]
