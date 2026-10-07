"""The few kinds of block that polled reads look for, as the SQL that finds them.

Each of these is the predicate of a partial index on `blocks` AND the filter of
the query that reads it, from this one object. That is what lets the planner use
the index at all: it only takes a partial index when the query's WHERE
implies the index's WHERE, and it proves that by comparing expressions, not
meanings. `Block.meta["options"].as_string()` means the same thing as
`meta ->> 'options'`, but it compiles to `CAST((meta ->> $1) AS VARCHAR)`
with the key and the value bound as parameters. asyncpg keeps each statement
prepared, and after five runs on a connection PostgreSQL may switch it to a
generic plan, which cannot see what those parameters hold — so it cannot prove
the index's WHERE and passes the index by. An EXPLAIN with the values typed in
plans like those first five runs, so it can show the index used while the
cached statement does not. So these are literal SQL, written once, and both
sides use them.

Each fragment names columns of `blocks` without a table prefix (an index
predicate cannot carry one), so a query using it must read `blocks` alone.

The migrations that build the indexes spell the same text out again, because a
migration is a snapshot and must not change when this file does;
`tests/integration/test_indexed_rows.py` fails when the two disagree.
"""

from sqlalchemy import text

#: Platform events on the machine side said in the conversation. When one
#: landed during a wait, the member is most likely not stuck: the machine under
#: it is not ready yet, and the sidebar picks a longer threshold and different
#: words for it. A machine the turn is waiting for is a run record
#: (`run_record.models.RunRecord`, kind `device_waiting`) and read from there.
MACHINE_EVENTS = ("environment_repaired",)

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

#: A message that named an agent and has not had its turn: never read into a
#: prompt, never answered or refused another way. The rows the pending-message
#: scan starts turns from (`pending_messages.queued_messages`), kept in a partial
#: index because that scan runs on a clock and the rest of the table is
#: everything ever said.
QUEUED_MESSAGE_ROWS = text(
    "kind = 'message'"
    " AND ((meta -> 'agent_recipient') ->> 'mentioned') = 'true'"
    " AND (meta -> 'consumed_turn') IS NOT NULL"
    " AND (meta ->> 'consumed_turn') IS NULL"
    " AND COALESCE(meta ->> 'prompt_attempts', '0') = '0'"
    " AND (meta -> 'delivery_event_id') IS NULL"
    " AND (meta -> 'answer_to') IS NULL"
)

#: The hook event id a materialized block carries, as `has_any_eid` reads it
#: and `ix_blocks_conversation_eid` is built on. Nine blocks in ten carry one;
#: the lookup asks whether a conversation already holds an id, and without this
#: index an id it does not hold reads the whole table (873 ms on dev,
#: 2026-10-07, 442,504 blocks).
EID = text("(meta ->> 'eid')")

#: A coalesced message: one block that landed several hook events, listed in
#: `meta.eids`. About 2,300 of the 442,000 blocks on dev.
COALESCED_ROWS = text("(meta -> 'eids') IS NOT NULL")
