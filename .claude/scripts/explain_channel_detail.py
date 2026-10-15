"""Why one channel's detail request costs what it does, in one run of EXPLAINs.

The channel detail response was dominated by one statement: the one behind the
"someone replied to me and I have not dealt with it" marker
(`_replied_to_me_statement`). Its WHERE clause ORs the two ways a block can
belong to a room — the block hangs off the room itself, or off a thread of the
room. Written the second way as the outer-joined `threads.room_id IN (…)`, the
planner cannot know `blocks.conversation_id` is constrained by both branches,
and seq-scans all of `blocks`.

Every plan runs against the same room in the same run, so the numbers are
comparable to each other:

  A   the statement exactly as the app compiles it now — the fix, in place: both
      branches constrain `blocks.conversation_id`, so the room's own blocks and
      its threads' blocks are two bitmaps against the same index.
  N   the shape it had before, restored textually: the second branch back to the
      outer-joined `threads.room_id IN (…)`, with the room id handed to it.
      This is the `Seq Scan on blocks` the fix removed, and the "before" column
      of the comparison. The rewrite is safe only while that branch is the
      statement's last parameter; the asserts below are the guard, and if the
      statement ever grows a parameter after it they raise rather than explain a
      query the app never ran.
  C   the room's own blocks alone, as one index scan — the shape A reaches.
  D1-3  three rewrites of that branch as a subquery, i.e. what you would try
      first without touching the call site. All three stay on the seq scan,
      which is why the call site reads the room's thread ids itself.

Env: PERF_DB_URL and PERF_HANDLE, same defaults as `concurrent_read_probe.py`.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "backend"))

import asyncpg  # noqa: E402
from sqlalchemy.dialects.postgresql import asyncpg as asyncpg_dialect  # noqa: E402

from app.domain.topic.repositories import _replied_to_me_statement  # noqa: E402
from concurrent_read_probe import DB_URL, VIEWER, discover_ids  # noqa: E402

DIALECT = asyncpg_dialect.dialect()

#: What the fix could replace the OR's thread branch with, one substitution
#: each. Grammar: `<placeholder>` is the branch's own leftover parameter.
#:
#: D1 is written the way the repository already writes this relation — the
#: thread's own id as a second subquery, `Block.conversation_id IN (…)`. Both
#: branches then constrain `blocks.conversation_id`, the leading column of every
#: index on `blocks`.
#:
#: D2 keeps the existing `threads.id = blocks.conversation_id` equality and adds
#: a hashed subquery of the room's thread ids (`= ANY (…)`) beside it. It does not
#: help either: the plan is still `Seq Scan on blocks` at 122.6 ms.
#:
#: D3 makes the room's own blocks an explicit `IN` list too, so the whole OR is
#: three seekable branches and no correlated `threads` join is needed.
PROPOSALS: list[tuple[str, str]] = [
    (
        "1",
        " OR blocks.conversation_id IN (SELECT t.id FROM threads AS t"
        " WHERE t.room_id = $inner)",
    ),
    (
        "2",
        " OR (threads.id = blocks.conversation_id AND blocks.conversation_id"
        " = ANY (SELECT t.id FROM threads AS t WHERE t.room_id = $inner))",
    ),
    (
        "3",
        " OR blocks.conversation_id = ANY (SELECT t.id FROM threads AS t"
        " WHERE t.room_id = $inner)",
    ),
]


def positional_arguments(compiled) -> list[object]:
    """The values to hand asyncpg, converted the way the driver converts them.

    asyncpg speaks `$1`-style and takes Python values, so the two enums go out
    as their string value and the JSONB containment operand goes out as the JSON
    text its type would have serialised to.
    """
    arguments: list[object] = []
    for name in compiled.positiontup or []:
        value = compiled.params[name]
        if isinstance(value, list):
            value = json.dumps(value)
        elif hasattr(value, "value") and isinstance(value.value, str):
            value = value.value
        arguments.append(value)
    return arguments


async def main() -> None:
    ids = await discover_ids()

    dsn = DB_URL.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        # The room the probe watches: the one with the most tasks. The seeded
        # database gives it no threads at all, so plan A's second branch is an
        # empty `IN` — the case that has to keep working, and the one the fix
        # used to seq-scan on.
        room = ids["room"]

        # The room's thread ids, read the way the app reads them: one indexed
        # query on `ix_threads_room_id`, handed to the statement as its second
        # list. Before the fix this query did not exist and the room id itself
        # went in that slot.
        #
        # This seeded room has no threads, and an EMPTY list is not the same
        # statement: `expanding` renders no placeholder at all, so the branch
        # disappears and plan N below would have nothing to rewrite. Two
        # synthetic ids stand in for a threaded room, which is the case the fix
        # is for and the shape the numbers are compared in. Both branches key on
        # `blocks.conversation_id` whichever ids go in, so the plan does not
        # depend on them matching a row.
        real = await conn.fetch("SELECT id FROM threads WHERE room_id = $1::uuid", room)
        thread_ids = (
            [str(r["id"]) for r in real]
            if real
            else [
                "00000000-0000-0000-0000-000000000001",
                "00000000-0000-0000-0000-000000000002",
            ]
        )
        statement = _replied_to_me_statement(VIEWER).params(
            replied_rooms=[room], replied_inner=thread_ids
        )
        compiled = statement.compile(
            dialect=DIALECT, compile_kwargs={"render_postcompile": True}
        )
        sql = str(compiled)
        order = list(compiled.positiontup or [])
        arguments = positional_arguments(compiled)

        print(f"viewer={VIEWER} room={room} project={ids['project']}")
        print(f"parameters, in placeholder order: {order}")

        blocks_here = await conn.fetchval(
            "SELECT count(*) FROM blocks WHERE conversation_id = $1::uuid", room
        )
        print(
            f"this room holds {blocks_here} blocks directly and {len(real)} threads"
            + (
                f" (plan A is run with the {len(thread_ids)} synthetic ids below)"
                if not real
                else ""
            )
        )

        print("\n" + "=" * 72)
        print("A  the statement as the app runs it (the fix)")
        print("=" * 72)
        for row in await conn.fetch("EXPLAIN (ANALYZE, BUFFERS) " + sql, *arguments):
            print(row[0])

        # N: back to the shape that seq-scanned. Safe to rewrite textually only
        # while that branch is the statement's last parameters and the app still
        # hands it a plain list; the asserts are the guard, and if either stops
        # holding they raise rather than explain a query that never ran. The
        # arguments go back to room ids, which is what the old statement's
        # second list held (`replied_to_me` passed `rooms` to both).
        inner_slots = [i + 1 for i, name in enumerate(order) if name.startswith("replied_inner_")]
        assert inner_slots, "the statement has no replied_inner parameter"
        assert inner_slots == list(range(inner_slots[0], len(arguments) + 1)), (
            inner_slots,
            len(arguments),
        )
        inners = ", ".join(f"${slot}::UUID" for slot in inner_slots)
        fragment = f" OR blocks.conversation_id IN ({inners})"
        assert sql.count(fragment) == 1, fragment
        old_sql = sql.replace(fragment, f" OR threads.room_id IN ({inners})")
        old_arguments = [*arguments[: inner_slots[0] - 1], *([room] * len(inner_slots))]
        print("\n" + "=" * 72)
        print(
            "N  before the fix, restored: "
            f"`{fragment.strip()}` back to `threads.room_id IN ({inners})`,"
            " room ids in those slots"
        )
        print("=" * 72)
        for row in await conn.fetch(
            "EXPLAIN (ANALYZE, BUFFERS) " + old_sql, *old_arguments
        ):
            print(row[0])

        print("\n" + "=" * 72)
        print("C  the room's own blocks alone")
        print("=" * 72)
        for row in await conn.fetch(
            "EXPLAIN (ANALYZE, BUFFERS) "
            "SELECT blocks.conversation_id FROM blocks "
            "WHERE blocks.conversation_id = $1::uuid",
            room,
        ):
            print(row[0])

        # D1-D3: the OR's second branch as a subquery, which is what you reach
        # for without touching the call site. Each takes ONE room id — the shape
        # the branch had before the fix, where the room ids went in — so this
        # runs against the first inner slot alone and drops the rest.
        inner = inner_slots[0]
        d_fragment = f" OR blocks.conversation_id IN (${inner}::UUID" + "".join(
            f", ${slot}::UUID" for slot in inner_slots[1:]
        ) + ")"
        assert sql.count(d_fragment) == 1, d_fragment
        for label, substitute in PROPOSALS:
            proposed = sql.replace(
                d_fragment, substitute.replace("$inner", f"${inner}")
            )
            assert proposed != sql
            print("\n" + "=" * 72)
            print(f"D{label}  {substitute.replace('$inner', f'${inner}').strip()}")
            print("=" * 72)
            for row in await conn.fetch(
                "EXPLAIN (ANALYZE, BUFFERS) " + proposed, *arguments[:inner]
            ):
                print(row[0])

        # The real thread ids of this room, read the way `replied_to_me` reads
        # them: one indexed query on `ix_threads_room_id`. The counts below are
        # what plan A above has to rank against plan N with, so the run has to
        # say how many there were. The seeded database has none in this room, so
        # A's second branch is an empty `IN` here — the case that has to keep
        # working — and a threaded room is what the ids stand for.
        real = await conn.fetch("SELECT id FROM threads WHERE room_id = $1::uuid", room)
        print(
            f"\n(the room's thread ids, read for plan A's second branch:"
            f" {len(real)} rows)"
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
