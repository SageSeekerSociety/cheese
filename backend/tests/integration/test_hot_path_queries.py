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

import re

import pytest
from sqlalchemy import text

from app.domain.room_task.services import TaskService
from tests.integration.conftest import (
    a_team,
    join_project_team,
    open_task,
    post_message,
    post_project,
)
from tests.support.counting_session import counting_sql

OWNER = "roster-owner"
PROJECT = "00000000-0000-0000-0000-0000000000aa"
TOPIC_ONE = "00000000-0000-0000-0000-000000000001"


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
    r = post_project(client, json={"name": name}, owner=OWNER)
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _add_members(client, bearer, project_id: str, handles: list[str]) -> None:
    for handle in handles:
        join_project_team(client, project_id, handle)


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

    with counting_sql(client) as small:
        assert client.get(f"/projects/{project_id}/members").status_code == 200
    small_count = len(small)

    _add_members(client, bearer, project_id, [f"m{i:02d}" for i in range(3, 15)])

    with counting_sql(client) as large:
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
                },
                headers=headers,
            )
            assert r.status_code == 200

    _make(3)
    url = f"/topics?project_id={project_id}&sort=last_activity_at&order=desc"
    with counting_sql(client) as small:
        assert client.get(url, headers=headers).status_code == 200
    small_count = len(small)
    # Pin the absolute cost of this fixture's four-row list (three rooms plus
    # the root the project creates). Every statement here is one a sidebar poll
    # pays on every tick, so adding one is a deliberate act, not a rounding
    # error — the relative check below would let a constant cost slip through.
    # One of them reads which rooms' sessions their runners declare running
    # (`session_states`), the board cell's 运行中. Three read which seat each
    # room's teammate sits in (`addressable_agent_handles`: the rosters, and who
    # on them is an agent), which a row's phrase names (「{agent}在做」).
    assert small_count == 34, (
        f"the topic list cost 34 statements; it now costs {small_count}.\n"
        + "\n".join(small)
    )

    _make(9)
    with counting_sql(client) as large:
        r = client.get(url, headers=headers)
    assert r.status_code == 200
    assert len(r.json()["data"]["data"]) >= 12

    assert len(large) == small_count, (
        f"topic count grew 3 -> 12 and queries went {small_count} -> "
        f"{len(large)}; something is querying per topic.\n" + "\n".join(large)
    )


def test_topic_list_reads_room_tasks_once(client, bearer, monkeypatch):
    """The list asks which tasks live in its rooms once per request.

    ``_awaiting_an_answer`` folds a room's tasks into its 待答 set, and the
    route folds the same tasks into the red rail's unresolved failures. Each
    consumer used to read and reassemble the mapping; now the second one takes
    the first one's answer.
    """
    project_id = _create_project(client, "Reads once")
    headers = bearer(OWNER)
    room_ids = []
    for i in range(2):
        r = client.post(
            "/topics",
            json={"project_id": project_id, "title": f"r{i}"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        room_ids.append(r.json()["data"]["id"])
    open_task(client, room_ids[0], "一件活", owner=OWNER)

    calls: list[list[str]] = []
    original = TaskService.tasks_of_rooms

    async def counted(self, room_ids):
        calls.append(list(room_ids))
        return await original(self, room_ids)

    monkeypatch.setattr(TaskService, "tasks_of_rooms", counted)
    response = client.get(f"/topics?project_id={project_id}", headers=headers)
    assert response.status_code == 200, response.text
    assert len(calls) == 1, (
        f"the list asked for its rooms' tasks {len(calls)} times; "
        "the second consumer rebuilt the mapping instead of reusing it."
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
                json={"project_id": project_id, "title": f"r{i}"},
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
            json={"project_id": project_id, "title": f"r{i}"},
            headers=headers,
        )
        ids.append(r.json()["data"]["id"])

    oldest = ids[0]
    # `/weekly` lands a real block in a topic.
    assert (
        client.post(
            f"/topics/{oldest}/weekly",
            json={"body": "hello"},
            headers=headers,
        ).status_code
        == 200
    )

    rows = _seeded_rooms(client, project_id, headers)
    assert rows[0]["id"] == oldest


# --------------------------------------------------------------------------
# POST /topics/{id}/messages
# --------------------------------------------------------------------------

#: The write that used to follow the INSERT: a participant's message patched
#: its own `turn_id` after the flush, so SQLAlchemy sent an UPDATE and then had
#: to read the row back.
_SELF_REFERENCE_UPDATE = re.compile(
    r"^\s*UPDATE blocks\s+SET\s+turn_id\s*=", re.IGNORECASE
)

#: Whole-row reads of one block by id: the route re-reads the anchor it just
#: wrote to answer the POST, and the repository used to read it once more
#: after the flush. A send may cost the route's read, not two.
_BLOCK_BY_ID = re.compile(r"\bFROM blocks WHERE blocks\.id = ")


def test_a_sent_message_groups_under_itself_without_a_second_write(client, bearer):
    """A message block is written once, its self-reference included.

    Where a block groups is a rule a reader can state without the code: one
    send is one group, and a person's message is its own. *How many statements*
    it takes to store that is what this pins — patching `turn_id` after the
    insert changed nothing visible, so only the statements show whether the
    value was written with the row or fixed up afterwards.
    """
    project_id = _create_project(client, "Sends")
    headers = bearer(OWNER)
    r = client.post(
        "/topics",
        json={"project_id": project_id, "title": "发送"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    topic_id = r.json()["data"]["id"]

    with counting_sql(client) as statements:
        sent = post_message(client, topic_id, OWNER, {"content": "一条消息"})

    patched = [s for s in statements if _SELF_REFERENCE_UPDATE.match(s)]
    assert not patched, (
        "the message's self-reference was written as a second statement:\n"
        + "\n".join(patched)
    )
    reads = [s for s in statements if _BLOCK_BY_ID.search(s)]
    assert len(reads) == 1, (
        "the write re-read its own row; only the route's read of the anchor "
        "it returns should ask for it by id:\n" + "\n".join(reads)
    )
    client_id_scans = [
        s
        for s in statements
        if "FROM blocks" in s
        and "blocks.author =" in s
        and "ORDER BY blocks.created_at, blocks.id" in s
    ]
    assert not client_id_scans, (
        "sending a message scanned blocks by author/client_id instead of "
        "relying on idempotency_keys:\n" + "\n".join(client_id_scans)
    )
    topic_by_id = [s for s in statements if "FROM topics WHERE topics.id =" in s]
    assert len(topic_by_id) == 1, (
        "the route and writer read the room row more than once:\n"
        + "\n".join(topic_by_id)
    )
    # The caller still gets the whole row back from that one INSERT.
    assert sent["seq"] > 0
    assert sent["turn_id"] == sent["id"]
    assert sent["created_at"]


# --------------------------------------------------------------------------
# The index behind 话题级未读
# --------------------------------------------------------------------------

# The exact predicate `TopicRepository.unread_counts` applies to `blocks`:
# one room's own line, messages only, written by somebody else.
_UNREAD_PREDICATE = """
    SELECT conversation_id, count(*) FROM blocks
    WHERE conversation_id = :conversation_id
      AND kind = 'message'
      AND author <> :handle
    GROUP BY conversation_id
"""


async def _seed_blocks(session, *, topics: int = 40, blocks: int = 4000) -> None:
    """Enough rows, with statistics, for the planner to make a real choice.

    On an empty table every index costs the same and the plan says nothing.
    """
    await session.execute(
        text(
            "INSERT INTO projects (name,owner_handle,team_id,ai_mode,settings,id,"
            "created_at,updated_at) VALUES ('p','o',:team,'collaborative',"
            "'{}',:pid,now(),now())"
        ),
        {"pid": PROJECT, "team": await a_team(session)},
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
            "INSERT INTO blocks (project_id,conversation_id,kind,author_type,author,"
            "content,refs,id,created_at,updated_at) SELECT :pid,"
            "('00000000-0000-0000-0000-'||lpad(((g%:t)+1)::text,12,'0'))::uuid,"
            "CASE WHEN g%3=0 THEN 'event' ELSE 'message' END,'participant','u'||(g%7),"
            "repeat('x',200),'[]',gen_random_uuid(),"
            "now()-(g||' minutes')::interval,now() "
            "FROM generate_series(1,:n) g"
        ),
        {"pid": PROJECT, "t": topics, "n": blocks},
    )
    await session.execute(text("ANALYZE blocks"))


async def _plan(session, sql: str, params: dict) -> str:
    rows = (await session.execute(text("EXPLAIN " + sql), params)).all()
    return "\n".join(row[0] for row in rows)


@pytest.mark.anyio
async def test_the_badge_query_matches_kind_in_the_index(db_factory):
    """The database must narrow on kind itself, not by reading rows.

    This is the difference the index makes, and it is invisible in the answer:
    the badge numbers were always right. With only a ``conversation_id``-leading index
    the plan carried ``kind`` as a ``Filter``, which means
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
            {"conversation_id": TOPIC_ONE, "handle": "nobody"},
        )

    assert "Seq Scan on blocks" not in plan, f"reads the whole table:\n{plan}"
    conditions = "\n".join(line for line in plan.splitlines() if "Cond:" in line)
    assert "kind" in conditions, f"kind is not matched by an index:\n{plan}"


@pytest.mark.anyio
async def test_a_topics_timeline_is_still_indexed_without_the_single_column_index(
    db_factory,
):
    """Dropping the single-column conversation index must not make a lookup a scan.

    Two composite indexes lead with ``conversation_id``, so either can answer a
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
                    text("EXPLAIN SELECT id FROM blocks WHERE conversation_id = :t"),
                    {"t": "00000000-0000-0000-0000-000000000001"},
                )
            ).all()
        )

    assert "Index" in plan and "Seq Scan" not in plan, plan
