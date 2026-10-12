"""A close hands its PR work off on the session's own commit, and not before.

``close_task`` stages ``close_pr_after_commit``, which spawns ``close_task_pr``
once the task is committed closed. The work must not be handed off earlier:
``close_task_pr`` opens a session of its own and reads the task, so a hand-off
that lands before the commit finds the task still open, gives up, and is never
retried — the closed task's PR stays open.

Something in a request releases a savepoint (``review.task_landing.tell_origin``
wraps its courtesy line in one), and SQLAlchemy dispatches ``after_commit`` when
a savepoint is released too. Handing the work to a one-shot ``after_commit``
listener therefore spent it on that savepoint. This pins the use site rather
than the helper: ``test_a_released_savepoint_is_not_a_commit.py`` says what
``after_own_commit`` must do, and this says that ``close_pr_after_commit`` asks
it to do so. Reverting that one call to a one-shot listener fails here.

``test_a_closed_task_closes_its_pr.py`` closes a PR end to end, so it answers
what the work does once it runs. This file answers when it was handed off.
"""

import asyncio
import uuid

from app.core import background
from app.domain.review.task_pr import close_pr_after_commit


class _Task:
    """A task as ``close_pr_after_commit`` reads one: an id, a project, a PR."""

    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.project_id = uuid.uuid4()
        self.pr_number = 77


class _Held:
    """A ``background.spawn`` that writes down what it was handed and runs none."""

    def __init__(self) -> None:
        self.names: list[str | None] = []

    def capture(self, coro, *, name: str | None = None) -> bool:
        self.names.append(name)
        coro.close()  # never awaited: nothing is run by this test
        return True


def test_a_savepoint_release_hands_off_nothing_and_the_commit_does(
    db_session, _portal, monkeypatch
):
    held = _Held()
    monkeypatch.setattr(background, "spawn", held.capture)

    async def scenario() -> tuple[list[str | None], list[str | None]]:
        close_pr_after_commit(db_session, _Task())
        async with db_session.begin_nested():
            pass  # released: after_commit fires, and nothing was committed
        at_release = list(held.names)
        await db_session.commit()
        for _ in range(20):  # the hand-off is spawned, not awaited here
            await asyncio.sleep(0)
        return at_release, list(held.names)

    at_release, at_commit = _portal.call(scenario)

    assert at_release == [], "the close handed its work off on a savepoint release"
    assert at_commit == ["close a closed task's PR"]
