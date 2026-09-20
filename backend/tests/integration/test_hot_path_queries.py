"""The polled endpoints must not get more expensive as a project fills up.

These are the requests a user actually waits on: the sidebar polls the topic
list and the two unread maps every 30 seconds, and opening a room fetches its
timeline. What makes them worth pinning is not their absolute speed — that
belongs to a benchmark, not a test suite — but a shape: **the work must not
scale with the size of the thing being listed.**

Both failures this file guards against passed every functional test there was.
A roster endpoint that issues one query per member returns exactly the right
JSON; a badge query with no index it can use returns exactly the right counts.
They are only visible as a count of round trips, and as whether the database
can answer without reading the table, so that is what these assert.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import Engine

OWNER = "roster-owner"
PROJECT = "00000000-0000-0000-0000-0000000000aa"
TOPIC_ONE = "00000000-0000-0000-0000-000000000001"


@contextmanager
def counting_sql() -> Iterator[list[str]]:
    """Every SQL statement issued while the block runs, in order.

    Listens on the ``Engine`` class rather than one instance: the app's session
    and the fixture's own both end up on sync engines underneath, and pinning
    the count means catching whatever the request actually issued.
    """
    seen: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        seen.append(" ".join(statement.split()))

    event.listen(Engine, "after_cursor_execute", _record)
    try:
        yield seen
    finally:
        event.remove(Engine, "after_cursor_execute", _record)


def _seeded_rooms(client, project_id: str, headers: dict) -> list[dict]:
    """The rooms a test created, newest-activity first.

    A project auto-creates a root topic; it is not one of the seeded rooms and
    would otherwise sit in the middle of every ordering assertion.
    """
    r = client.get(
        f"/topics?project_id={project_id}&sort=last_activity_at&order=desc",
        headers=headers,
    )
    assert r.status_code == 200
    return [t for t in r.json()["data"]["data"] if t["kind"] != "root"]


def _create_project(client, name: str = "Hot path") -> str:
    r = client.post("/projects", json={"name": name, "owner_handle": OWNER})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _add_members(client, bearer, project_id: str, handles: list[str]) -> None:
    for handle in handles:
        r = client.post(
            f"/projects/{project_id}/members",
            json={"user_handle": handle},
            headers=bearer(OWNER),
        )
        assert r.status_code == 200


# --------------------------------------------------------------------------
# GET /projects/{id}/members
# --------------------------------------------------------------------------


def test_roster_round_trips_do_not_grow_with_the_roster(client, bearer):
    """Four times the members must not mean four times the queries.

    This is the whole N+1: the endpoint answered correctly either way, and the
    only symptom was that a 30-person project spent 30 round trips learning 30
    user ids it could have asked for once.
    """
    project_id = _create_project(client)
    _add_members(client, bearer, project_id, [f"m{i:02d}" for i in range(3)])

    with counting_sql() as small:
        assert client.get(f"/projects/{project_id}/members").status_code == 200
    small_count = len(small)

    _add_members(client, bearer, project_id, [f"m{i:02d}" for i in range(3, 15)])

    with counting_sql() as large:
        r = client.get(f"/projects/{project_id}/members")
    assert r.status_code == 200
    # 15 members + the owner, so a per-member query would be plainly visible.
    assert r.json()["data"]["total"] >= 15

    assert len(large) == small_count, (
        f"roster grew from 3 to 15 members and the query count went "
        f"{small_count} -> {len(large)}; something is querying per member.\n"
        + "\n".join(large)
    )


def test_roster_still_reports_names_and_agent_flags(client, bearer):
    """The batched lookup must answer exactly what the per-member one did."""
    project_id = _create_project(client)
    _add_members(client, bearer, project_id, ["alice", "bob"])

    rows = {
        m["user_handle"]: m
        for m in client.get(f"/projects/{project_id}/members").json()["data"]["data"]
    }

    assert {"alice", "bob"} <= rows.keys()
    for handle in ("alice", "bob"):
        # A handle with no fusion profile behind it still gets a name (its own
        # handle) and an explicit non-agent verdict — never a missing key.
        assert rows[handle]["name"]
        assert rows[handle]["agent"] is False
        assert "avatar_id" in rows[handle]


def test_roster_of_a_project_with_one_member(client, bearer):
    """The batch path must not degenerate on the smallest roster."""
    project_id = _create_project(client)
    body = client.get(f"/projects/{project_id}/members").json()["data"]
    assert body["total"] == len(body["data"])


# --------------------------------------------------------------------------
# GET /topics?project_id=…
# --------------------------------------------------------------------------


def test_topic_list_round_trips_do_not_grow_with_the_topic_count(client, bearer):
    """Listing 12 topics must cost the same number of queries as listing 3.

    Every field the sidebar needs (last activity, relevance, live cards) is a
    batch lookup, and this is what keeps it that way — each of them is a
    plausible place for someone to add a per-topic probe.
    """
    project_id = _create_project(client)
    headers = bearer(OWNER)

    def _make(n: int) -> None:
        for i in range(n):
            r = client.post(
                "/topics",
                json={
                    "project_id": project_id,
                    "title": f"room {i}",
                    "created_by": OWNER,
                },
                headers=headers,
            )
            assert r.status_code == 200

    _make(3)
    url = f"/topics?project_id={project_id}&sort=last_activity_at&order=desc"
    with counting_sql() as small:
        assert client.get(url, headers=headers).status_code == 200
    small_count = len(small)

    _make(9)
    with counting_sql() as large:
        r = client.get(url, headers=headers)
    assert r.status_code == 200
    assert len(r.json()["data"]["data"]) >= 12

    assert len(large) == small_count, (
        f"topic count grew 3 -> 12 and queries went {small_count} -> "
        f"{len(large)}; something is querying per topic.\n" + "\n".join(large)
    )


def test_topic_list_reports_last_activity_for_every_row(client, bearer):
    """Deriving activity in the same query that sorts by it must not drop it.

    A room nobody has spoken in falls back to its own creation, so the field is
    never null — sorting and filtering on it stay total.
    """
    project_id = _create_project(client)
    headers = bearer(OWNER)
    for i in range(3):
        assert (
            client.post(
                "/topics",
                json={"project_id": project_id, "title": f"r{i}", "created_by": OWNER},
                headers=headers,
            ).status_code
            == 200
        )

    rows = _seeded_rooms(client, project_id, headers)

    assert rows
    for row in rows:
        assert row["last_activity_at"], f"no last_activity_at on {row['title']}"
    # Descending order actually applied, using the value that came back.
    stamps = [row["last_activity_at"] for row in rows]
    assert stamps == sorted(stamps, reverse=True)


def test_talking_in_a_room_moves_it_to_the_top(client, bearer):
    """last_activity_at tracks the newest block, not the topic row's mtime."""
    project_id = _create_project(client)
    headers = bearer(OWNER)
    ids = []
    for i in range(3):
        r = client.post(
            "/topics",
            json={"project_id": project_id, "title": f"r{i}", "created_by": OWNER},
            headers=headers,
        )
        ids.append(r.json()["data"]["id"])

    oldest = ids[0]
    # `/decision` is how the rest of the suite lands a real block in a topic.
    assert (
        client.post(
            f"/topics/{oldest}/decision",
            json={"decision": "hello"},
            headers=headers,
        ).status_code
        == 200
    )

    rows = _seeded_rooms(client, project_id, headers)
    assert rows[0]["id"] == oldest


# --------------------------------------------------------------------------
# The index behind 话题级未读
# --------------------------------------------------------------------------

# The exact predicate `TopicRepository.unread_counts` applies to `blocks`:
# one room's own line, messages only, written by somebody else.
_UNREAD_PREDICATE = """
    SELECT topic_id, count(*) FROM blocks
    WHERE topic_id = :topic_id
      AND kind = 'message'
      AND task_id IS NULL
      AND author <> :handle
    GROUP BY topic_id
"""


async def _seed_blocks(session, *, topics: int = 40, blocks: int = 4000) -> None:
    """Enough rows, with statistics, for the planner to make a real choice.

    On an empty table every index costs the same and the plan says nothing.
    """
    await session.execute(
        text(
            "INSERT INTO projects (name,owner_handle,ai_mode,settings,id,"
            "created_at,updated_at,summary) VALUES ('p','o','collaborative',"
            "'{}',:pid,now(),now(),'')"
        ),
        {"pid": PROJECT},
    )
    await session.execute(
        text(
            "INSERT INTO topics (project_id,title,kind,status,id,created_at,"
            "updated_at,is_private) SELECT :pid,'t'||g,'topic','active',"
            "('00000000-0000-0000-0000-'||lpad(g::text,12,'0'))::uuid,"
            "now(),now(),false FROM generate_series(1,:n) g"
        ),
        {"pid": PROJECT, "n": topics},
    )
    await session.execute(
        text(
            "INSERT INTO blocks (project_id,topic_id,kind,author_type,author,"
            "content,refs,id,created_at,updated_at,doc_version) SELECT :pid,"
            "('00000000-0000-0000-0000-'||lpad(((g%:t)+1)::text,12,'0'))::uuid,"
            "CASE WHEN g%3=0 THEN 'event' ELSE 'message' END,'human','u'||(g%7),"
            "repeat('x',200),'[]',gen_random_uuid(),"
            "now()-(g||' minutes')::interval,now(),1 "
            "FROM generate_series(1,:n) g"
        ),
        {"pid": PROJECT, "t": topics, "n": blocks},
    )
    await session.execute(text("ANALYZE blocks"))


async def _plan(session, sql: str, params: dict) -> str:
    rows = (await session.execute(text("EXPLAIN " + sql), params)).all()
    return "\n".join(row[0] for row in rows)


@pytest.mark.anyio
async def test_the_badge_query_matches_kind_and_task_in_the_index(db_factory):
    """The database must narrow on kind and task_id itself, not by reading rows.

    This is the difference the index makes, and it is invisible in the answer:
    the badge numbers were always right. With only a ``topic_id``-leading index
    the plan carried ``kind`` and ``task_id`` as a ``Filter``, which means
    fetching every one of a topic's blocks and discarding most of them — the
    read amplification that made this poll cost more as the whole platform
    grew. Carried as an index condition, only matching entries are ever
    touched.

    ``author`` is expected to remain a filter: it is an inequality, so it rides
    along in the index payload rather than narrowing the scan.

    What this deliberately does NOT assert is ``Heap Fetches: 0``. That is real
    and worth having — it is what the covering ``INCLUDE (author)`` buys — but
    it needs the visibility map to be current, which takes a VACUUM that a
    transactional test cannot run. It is verified in the perf report instead.
    """
    async with db_factory() as session:
        await _seed_blocks(session)
        plan = await _plan(
            session,
            _UNREAD_PREDICATE.strip(),
            {"topic_id": TOPIC_ONE, "handle": "nobody"},
        )

    assert "Seq Scan on blocks" not in plan, f"reads the whole table:\n{plan}"
    conditions = "\n".join(line for line in plan.splitlines() if "Cond:" in line)
    assert "kind" in conditions, f"kind is not matched by an index:\n{plan}"
    assert "task_id" in conditions, f"task_id is not matched by an index:\n{plan}"


@pytest.mark.anyio
async def test_a_topics_timeline_is_still_indexed_without_the_single_column_index(
    db_factory,
):
    """Dropping ``ix_blocks_topic_id`` must not make a topic lookup a scan.

    Two composite indexes lead with ``topic_id``, so either can answer a
    topic-only lookup — including the sweep a cascading delete performs when a
    topic or a whole project goes away. That path has no test of its own and
    degrades from milliseconds to minutes if it ever loses its index.
    """
    async with db_factory() as session:
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(
            row[0]
            for row in (
                await session.execute(
                    text("EXPLAIN SELECT id FROM blocks WHERE topic_id = :t"),
                    {"t": "00000000-0000-0000-0000-000000000001"},
                )
            ).all()
        )

    assert "Index" in plan and "Seq Scan" not in plan, plan
