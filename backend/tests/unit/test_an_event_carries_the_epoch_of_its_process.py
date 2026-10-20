"""An event a runner reports carries the epoch of the process it is about.

What a runner's own process does before the platform registers it (it came up,
it was written an input) is that process's, and takes the epoch the first
registration gives it: it is delivered then, once, as the session's own. What
it reports for the process before it (that one's ending, its unread inputs) is
about that process, and keeps the epoch that process was registered under, or
none: a registration of this runner is never that process's.
"""

import sqlite3

from app.domain.agent.harness.driven import lifecycle


def _outbox() -> lifecycle.Outbox:
    return lifecycle.Outbox(sqlite3.connect(":memory:"))


def test_what_a_runner_wrote_before_its_registration_takes_that_registration():
    outbox = _outbox()
    outbox.emit(lifecycle.STATUS, records=0, worker_status=lifecycle.RUNNING)
    # Not handed to anyone until a registration owns it.
    assert outbox.pending() == []

    outbox.register(3)

    assert [event["epoch"] for event in outbox.pending()] == [3]


def test_what_a_runner_reports_for_an_earlier_process_keeps_that_ones_epoch():
    outbox = _outbox()
    outbox.emit(lifecycle.EXITED, records=0, launch="earlier", epoch=2)
    outbox.emit(
        lifecycle.EXITED,
        records=0,
        launch="never-registered",
        epoch=lifecycle.NEVER_REGISTERED,
    )

    outbox.register(3)

    assert [(event["launch"], event["epoch"]) for event in outbox.pending()] == [
        ("earlier", 2),
        ("never-registered", lifecycle.NEVER_REGISTERED),
    ]
