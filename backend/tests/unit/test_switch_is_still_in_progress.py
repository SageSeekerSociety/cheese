"""Holds this branch shut while the switch is half-done. Delete to merge.

The migration on this branch deletes every work `topics` row and moves its
conversation onto the room. Until the code that CREATES work is pointed at
`tasks` too, merging that is not an incomplete feature — it is a database with
its history moved and an application still writing the old shape beside it.

So this fails on purpose, and it is the only thing failing. It goes away in the
commit that finishes the switch; if it is still here, the switch is not done,
whatever the rest of the suite says.
"""

import pytest


def test_switch_is_still_in_progress() -> None:
    pytest.fail(
        "the task switch is not finished — delete "
        "backend/tests/unit/test_switch_is_still_in_progress.py when it is"
    )
