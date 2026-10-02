"""The few kinds of block that polled reads look for, as the SQL that finds them.

Each of these is the predicate of a partial index on `blocks` AND the filter of
the query that reads it, from this one object. That is what lets the planner use
the index at all: it only takes a partial index when the query's WHERE
implies the index's WHERE, and it proves that by comparing expressions, not
meanings. `Block.meta["options"].as_string()` means the same thing as
`meta ->> 'options'`, but it compiles to `CAST((meta ->> $1) AS VARCHAR)` — a
bound key and a cast the index does not have — and the planner then reads the
whole table instead. So these are literal SQL, written once, and both sides
use them.

Each fragment names columns of `blocks` without a table prefix (an index
predicate cannot carry one), so a query using it must read `blocks` alone.

The migration that builds the indexes spells the same text out again, because a
migration is a snapshot and must not change when this file does;
`tests/integration/test_indexed_rows.py` fails when the two disagree.
"""

from sqlalchemy import text

#: Platform events on the machine side. When one landed during a wait, the
#: member is most likely not stuck: the machine under it is not ready yet, and
#: the sidebar picks a longer threshold and different words for it.
MACHINE_EVENTS = (
    "machine_provisioning",
    "device_waiting",
    "sandbox_rebuilt",
    "environment_repaired",
)

#: "This turn broke": an unclassified failure (HTTP 502/404, an exception's own
#: words) and a classified platform fault. Timeouts and deploy interruptions are
#: warnings — the platform carries on by itself — and do not count.
FAILED_TURN_EVENTS = ("turn_failed", "platform_error")


def _one_of(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


#: A question put to the room with buttons to answer it (`ask_options`).
QUESTION_ROWS = text("kind = 'message' AND (meta ->> 'options') IS NOT NULL")

#: A platform event saying the machine under a room is not ready yet.
MACHINE_EVENT_ROWS = text(f"(meta ->> 'event_type') IN ({_one_of(MACHINE_EVENTS)})")

#: A platform event saying a turn ended in an error.
FAILED_TURN_ROWS = text(
    f"(meta ->> 'event_type') IN ({_one_of(FAILED_TURN_EVENTS)})"
    " AND (meta ->> 'severity') = 'error'"
)
