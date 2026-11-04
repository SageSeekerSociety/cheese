"""A user read by name, and a profile joined to its user, go through an index.

`"user".username = $1` is how every signed-in request, the roster and the
seat read find a user; the only index on that column used to be over
`lower(username)`, which the bare equality cannot use. The joins from a user
to `user_profile` name no `deleted_at`, so the partial unique index on
`user_profile.user_id` could not serve them either. Both scanned the whole
table, on every such read.

With sequential scans priced out, a plan that still scans a whole table (or a
whole index, with no condition to seek on) has no index it can use."""

import asyncio
import json

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.domain.user.models import User, UserProfile


def _scans_without_a_seek(plan: dict, table: str) -> list[str]:
    found = []
    if plan.get("Relation Name") == table and (
        plan["Node Type"] == "Seq Scan"
        or (
            plan["Node Type"] in ("Index Scan", "Index Only Scan")
            and not plan.get("Index Cond")
        )
    ):
        found.append(plan["Node Type"])
    for child in plan.get("Plans", []):
        found += _scans_without_a_seek(child, table)
    return found


def _plan(client, stmt) -> dict:
    sql = str(
        stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )

    async def go() -> dict:
        async with client.test_factory() as db:
            conn = await (await db.connection()).get_raw_connection()
            raw = conn.driver_connection
            await raw.execute("SET enable_seqscan = off")
            try:
                out = await raw.fetchval("EXPLAIN (FORMAT JSON) " + sql)
            finally:
                await raw.execute("RESET enable_seqscan")
            return (json.loads(out) if isinstance(out, str) else out)[0]["Plan"]

    return asyncio.run(go())


def test_a_user_is_read_by_name_through_an_index(client):
    stmt = select(User).where(User.username == "alice", User.deleted_at.is_(None))
    assert _scans_without_a_seek(_plan(client, stmt), "user") == []


def test_a_profile_is_joined_to_its_user_through_an_index(client):
    stmt = (
        select(User.username, UserProfile.nickname)
        .outerjoin(UserProfile, UserProfile.user_id == User.id)
        .where(User.id == 1)
    )
    assert _scans_without_a_seek(_plan(client, stmt), "user_profile") == []
