"""Why one channel's detail request costs what it does, in one run of EXPLAINs.

The channel detail response is dominated by one statement: the one behind the
"someone replied to me and I have not dealt with it" marker
(`_replied_to_me_statement`). Its WHERE clause ORs the two ways a block can
belong to a room — the block hangs off the room itself, or off a thread of the
room — and that OR is what keeps the planner off `blocks`' indexes.

Every plan runs against the same room in the same run, so the numbers are
comparable to each other:

  A   the statement exactly as the app compiles it
  B   A with the OR's thread branch deleted: same joins, same other conditions,
      one fewer way in. The deletion is textual; the asserts below check that
      the dropped parameter is the statement's last one, so nothing renumbers.
  C   the room's own blocks alone, as one index scan — the shape B reaches.
  D1-3  three rewrites of the OR's thread branch as a subquery, i.e. what you
      would try first without touching the call site. All three stay on the
      seq scan.
  E   the fix that works: read the room's thread ids with one extra indexed
      query and pass them in as a second explicit `IN` list, the way the room
      ids already go in.

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
        # The room the probe watches: the one with the most tasks. It has no
        # threads, which is the case plan A wastes its scan on.
        room = ids["room"]

        statement = _replied_to_me_statement(VIEWER).params(
            replied_rooms=[room], replied_inner=[room]
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
        threads_here = await conn.fetchval(
            "SELECT count(*) FROM threads WHERE room_id = $1::uuid", room
        )
        print(
            f"this room holds {blocks_here} blocks directly and {threads_here} threads"
        )

        print("\n" + "=" * 72)
        print("A  the statement as the app runs it")
        print("=" * 72)
        for row in await conn.fetch("EXPLAIN (ANALYZE, BUFFERS) " + sql, *arguments):
            print(row[0])

        # B: delete the OR's thread branch. Safe to do textually only while that
        # branch is the statement's last parameter; the asserts are the guard,
        # and if the statement ever grows a parameter after it they raise rather
        # than explain a query the app never runs.
        inner = order.index("replied_inner_1") + 1
        fragment = f" OR threads.room_id IN (${inner}::UUID)"
        assert inner == len(arguments), (inner, len(arguments))
        assert sql.count(fragment) == 1, fragment
        print("\n" + "=" * 72)
        print(f"B  A minus `{fragment.strip()}` and its argument")
        print("=" * 72)
        for row in await conn.fetch(
            "EXPLAIN (ANALYZE, BUFFERS) " + sql.replace(fragment, ""),
            *arguments[: inner - 1],
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

        for label, substitute in PROPOSALS:
            print("\n" + "=" * 72)
            print(f"D{label}  {substitute.replace('$inner', f'${inner}').strip()}")
            print("=" * 72)
            proposed = sql.replace(
                fragment, substitute.replace("$inner", f"${inner}")
            )
            assert proposed != sql
            for row in await conn.fetch(
                "EXPLAIN (ANALYZE, BUFFERS) " + proposed, *arguments
            ):
                print(row[0])

        # E: the room's thread ids read by one extra indexed query and passed in
        # as a second list, the way `replied_to_me` already passes the room ids.
        # A subquery here is what D1-D3 got stuck on; a plain list of ids is
        # what the planner can rank against the room's own list.
        #
        # The seeded database has no rows in `threads` at all, so in this room
        # the real list is empty - and an empty list is exactly the case that
        # has to keep working. Two synthetic ids stand in for the room that has
        # threads, so the plan below is the one a threaded room gets. The `E`
        # line says which of the two ran.
        real = await conn.fetch("SELECT id FROM threads WHERE room_id = $1::uuid", room)
        if real:
            e_ids = [str(r["id"]) for r in real]
        else:
            e_ids = [
                "00000000-0000-0000-0000-000000000001",
                "00000000-0000-0000-0000-000000000002",
            ]
        e_list = ", ".join(f"'{i}'::uuid" for i in e_ids)
        print("\n" + "=" * 72)
        print(
            "E  the fix with the room's thread ids passed in as a list:"
            f" OR blocks.conversation_id IN ({e_list})"
        )
        print(
            f"   ({len(real)} real thread ids in this room; the two above are"
            " synthetic)"
            if not real
            else f"   ({len(real)} real thread ids in this room)"
        )
        print("=" * 72)
        for row in await conn.fetch(
            "EXPLAIN (ANALYZE, BUFFERS) "
            + sql.replace(fragment, f" OR blocks.conversation_id IN ({e_list})"),
            *arguments[: inner - 1],
        ):
            print(row[0])
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
