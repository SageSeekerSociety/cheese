"""Count the 现场 rows the removed writers left behind. Read-only.

Run through `docker exec -i` on dev with `DATABASE_URL` set; prints JSON counts
and never a credential.

A retired row is one of the three kinds the removed 现场 writers produced, each
a hidden event block (`meta.in_room` false) named by what it carries:

- a tool step (`meta.tool`), with the output, failure and calls later written
  onto it;
- a subagent's conclusion (`meta.subagent`);
- the session's narration (`meta.progress` true).

Other hidden event blocks are still written and still read: an agent notice
for an edited message or a memory change, a mention line. Release two deletes
`RETIRED_SITE_ROWS` and nothing wider.

No `run_records` writer was removed: platform statuses still use the table.
Its rows are counted by kind, so the dev count shows which kinds are there
and that each is one a live writer still produces.
"""

import asyncio
import json
import os

import asyncpg

#: The rows release two deletes, as a `WHERE` over `blocks`.
#: Containment and `?` are never NULL, so `NOT (...)` is its exact complement.
RETIRED_SITE_ROWS = """
    kind = 'event'
    AND meta::jsonb @> '{"in_room": false}'
    AND (
        meta::jsonb ? 'tool'
        OR meta::jsonb ? 'subagent'
        OR meta::jsonb @> '{"progress": true}'
    )
"""


async def main():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    db = await asyncpg.connect(dsn)
    try:
        async with db.transaction(readonly=True):
            blocks = await db.fetchrow(f"""
                SELECT count(*) FILTER (WHERE kind = 'event') AS all_event_blocks,
                  count(*) FILTER (WHERE {RETIRED_SITE_ROWS}) AS retired_site_rows,
                  count(*) FILTER (WHERE {RETIRED_SITE_ROWS} AND meta::jsonb ? 'tool') AS tool_rows,
                  count(*) FILTER (WHERE {RETIRED_SITE_ROWS} AND meta::jsonb ? 'subagent') AS subagent_rows,
                  count(*) FILTER (WHERE {RETIRED_SITE_ROWS} AND meta::jsonb @> '{{"progress": true}}') AS narration_rows,
                  count(*) FILTER (WHERE {RETIRED_SITE_ROWS} AND meta::jsonb ? 'output') AS rows_with_output,
                  count(*) FILTER (
                    WHERE kind = 'event' AND meta::jsonb @> '{{"in_room": false}}'
                      AND NOT ({RETIRED_SITE_ROWS})
                  ) AS hidden_rows_kept
                FROM blocks
            """)
            run_records = await db.fetch(
                "SELECT kind, count(*) AS n FROM run_records GROUP BY kind ORDER BY kind"
            )
            print(
                json.dumps(
                    {
                        **dict(blocks),
                        "run_records_by_kind": {r["kind"]: r["n"] for r in run_records},
                        "transaction_read_only": await db.fetchval(
                            "SHOW transaction_read_only"
                        ),
                    },
                    sort_keys=True,
                )
            )
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
