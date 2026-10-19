"""Run through docker exec -i on dev. Read-only counts; never emits credentials."""

import asyncio
import json
import os

import asyncpg


async def main():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    db = await asyncpg.connect(dsn)
    try:
        async with db.transaction(readonly=True):
            row = await db.fetchrow("""
                SELECT count(*) AS all_event_blocks,
                  count(*) FILTER (WHERE meta->>'in_room' = 'false') AS retired_site_rows,
                  count(*) FILTER (WHERE meta->>'in_room' = 'false' AND meta::jsonb ? 'tool') AS tool_rows,
                  count(*) FILTER (WHERE meta->>'in_room' = 'false' AND meta::jsonb ? 'subagent') AS subagent_rows,
                  count(*) FILTER (WHERE meta->>'in_room' = 'false' AND meta->>'progress' = 'true') AS narration_rows,
                  count(*) FILTER (WHERE meta->>'in_room' = 'false' AND meta::jsonb ? 'output') AS rows_with_output
                FROM blocks WHERE kind = 'event'
            """)
            print(json.dumps(dict(row), sort_keys=True))
            print(json.dumps({"run_records_retired": 0, "transaction_read_only": await db.fetchval("SHOW transaction_read_only")}))
    finally:
        await db.close()


asyncio.run(main())
