"""Seed a dev-shaped dataset so ``/awaiting-me`` can be measured and profiled.

The point is not a pretty demo — it is *row counts and query shapes* close to
production, because the endpoint's cost is linear in how many rows it has to
fetch and fold. The tracker's target shape (2026-10-12) is:

  * 13 projects, main project with 1437 活 of which 1355 closed;
  * ~600 channels reachable by the viewer;
  * ~480k message blocks;
  * 1500+ acceptance cards;
  * the viewer is the owner/reporter of most work, a few open items address him.

Everything is written straight through asyncpg (``COPY`` for blocks) so a run
takes seconds, not minutes. Idempotent: it TRUNCATEs the tables it owns.

Run from the repo root::

    uv run --project backend python scripts/perf-awaiting-me/seed_awaiting_me.py

DSN comes from ``PERF_DATABASE_URL`` (asyncpg form, ``postgresql://...``).
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import urllib.parse
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg

DSN = os.environ.get(
    "PERF_DATABASE_URL", "postgresql://cheesex:cheesex@127.0.0.1:5433/cheese_perf"
)

ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
FORBIDDEN_DBS = {"cheesex", "cheese", "postgres", "template0", "template1", "dev", "prod", "production"}


def assert_safe_perf_dsn(dsn: str) -> str:
    """Refuse to connect or truncate unless DSN targets an isolated local perf/test DB."""
    parsed = urllib.parse.urlsplit(dsn)
    host = (parsed.hostname or "").lower()
    dbname = parsed.path.lstrip("/")
    if host not in ALLOWED_HOSTS:
        raise RuntimeError(
            f"Refusing to seed non-local database host {host!r} (allowed: {sorted(ALLOWED_HOSTS)})"
        )
    lower_db = dbname.lower()
    if (
        not lower_db
        or lower_db in FORBIDDEN_DBS
        or ("perf" not in lower_db and "test" not in lower_db)
    ):
        raise RuntimeError(
            f"Refusing to truncate database {dbname!r}: name must contain 'perf' or 'test' "
            f"and not be a business database"
        )
    return dbname

#: The person the measurement asks for. Owns every project so ``list_visible_to``
#: returns all of them, the way the tracker's account sees the whole platform.
VIEWER = "perfviewer"
#: Other handles that show up as owners/reporters/reviewers so the candidate
#: filter has something to reject.
OTHERS = [f"perfuser{i}" for i in range(1, 13)]

# —— shape ——————————————————————————————————————————————————————————
N_PROJECTS = 13
MAIN_ROOMS = 200
MAIN_TASKS = 1437
MAIN_OPEN = 82
OTHER_ROOMS = 33
OTHER_TASKS = 40
OTHER_OPEN = 3
N_BLOCKS = 480_000
HOT_CONVERSATIONS = 30
HOT_BLOCKS = 12_000

SEED = 20261013

# —— the three boundary rooms ————————————————————————————————————————
#: Rooms the viewer can see that still must not reach the list, each shaped the
#: way that would put it there if its rule went unread. Without them the
#: endpoint is only ever measured on rows it is supposed to show, and a change
#: that starts listing an empty or archived room costs nothing in the diff.
#: They live in the main project only, and none of them is in its ``rooms``
#: list — task round-robin and block mass distribute over that list, so the
#: rest of the dataset stays the size it was.
EMPTY_ROOMS = 6
ARCHIVED_ROOMS = 6
AGENT_FOLLOWUP_ROOMS = 6
#: The handle that asked in the followed-up rooms. ``looks_like_agent_handle``
#: reads the ``cheese-`` prefix, and a follow-up only settles a question its own
#: **agent** walked away from: a person who asks and then adds a line has not
#: stopped owing an answer (#2046 is the agent's case).
AGENT = "cheese-perf"


def now() -> datetime:
    return datetime.now(UTC)


def main() -> None:
    asyncio.run(_main())


async def _main() -> None:
    expected_db = assert_safe_perf_dsn(DSN)
    rng = random.Random(SEED)
    conn = await asyncpg.connect(DSN)
    try:
        actual_db = await conn.fetchval("select current_database()")
        if actual_db != expected_db or ("perf" not in actual_db.lower() and "test" not in actual_db.lower()):
            raise RuntimeError(f"Refusing to truncate database {actual_db!r}")
        await _truncate(conn)
        team_id = await conn.fetchval("select nextval('team_seq')")
        await conn.execute(
            """insert into team (id, name, handle, intro, description, avatar_id,
                   created_at, updated_at, visibility, join_approval, plan_key)
               values ($1,'perf','perfteam','','',0,$2,$2,'public',true,'free')""",
            team_id,
            now(),
        )
        await _users(conn)
        projects = await _projects(conn, team_id, rng)
        await _fill(conn, rng, projects)
        await _notifications(conn, projects)
        await _report(conn)
    finally:
        await conn.close()


async def _truncate(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """truncate blocks, conversations, tasks, topics, threads, accept_cards,
                    projects, notification, team, users restart identity cascade"""
    )


async def _users(conn: asyncpg.Connection) -> None:
    rows = [
        (h, h, "bio", json.dumps([]), json.dumps([]), uuid.uuid4(), now(), now())
        for h in [VIEWER, *OTHERS]
    ]
    await conn.executemany(
        """insert into users (handle, name, bio, interests, skills, id,
               created_at, updated_at)
           values ($1,$2,$3,$4,$5,$6,$7,$8)""",
        rows,
    )


async def _projects(
    conn: asyncpg.Connection, team_id: int, rng: random.Random
) -> list[dict]:
    projects = []
    for i in range(N_PROJECTS):
        pid = uuid.uuid4()
        root = uuid.uuid4()
        rooms = MAIN_ROOMS if i == 0 else OTHER_ROOMS
        await conn.execute(
            """insert into projects (id, name, owner_handle, ai_mode, settings,
                   team_id, slug, agent_credential_epoch, intent, created_at,
                   updated_at, root_topic_id)
               values ($1,$2,$3,'collaborative','{}'::json,$4,$5,0,'',$6,$6,null)""",
            pid,
            f"proj{i}",
            VIEWER,
            team_id,
            f"p{i:04d}",
            now(),
        )
        await conn.execute(
            """insert into topics (id, project_id, parent_id, title, kind, status,
                   created_by, created_at, updated_at, is_private, members_only,
                   archived_with_project, number)
               values ($1,$2,null,$3,'root','active',$4,$5,$5,false,false,false,0)""",
            root,
            pid,
            f"proj{i}",
            VIEWER,
            now(),
        )
        await conn.execute(
            "update projects set root_topic_id=$1 where id=$2", root, pid
        )
        rooms_list = [root]
        for r in range(rooms):
            rid = uuid.uuid4()
            await conn.execute(
                """insert into topics (id, project_id, parent_id, title, kind,
                       status, created_by, created_at, updated_at, is_private,
                       members_only, archived_with_project, number)
                   values ($1,$2,$3,$4,'topic','active',$5,$6,$6,false,false,
                           false,$7)""",
                rid,
                pid,
                root,
                f"room {r}",
                VIEWER,
                now(),
                r + 1,
            )
            rooms_list.append(rid)
        boundary: dict[str, list[uuid.UUID]] = {}
        if i == 0:
            number = rooms
            for label, status, count in (
                ("empty room", "active", EMPTY_ROOMS),
                ("archived room", "archived", ARCHIVED_ROOMS),
                ("followed-up room", "active", AGENT_FOLLOWUP_ROOMS),
            ):
                ids = []
                for r in range(count):
                    rid = uuid.uuid4()
                    number += 1
                    await conn.execute(
                        """insert into topics (id, project_id, parent_id, title,
                               kind, status, created_by, created_at, updated_at,
                               is_private, members_only, archived_with_project,
                               number)
                           values ($1,$2,$3,$4,'topic',$5,$6,$7,$7,false,false,
                                   false,$8)""",
                        rid,
                        pid,
                        root,
                        f"{label} {r}",
                        status,
                        VIEWER,
                        now(),
                        number,
                    )
                    ids.append(rid)
                boundary[label] = ids
        projects.append(
            {
                "id": pid,
                "root": root,
                "rooms": rooms_list,
                "index": i,
                "boundary": boundary,
            }
        )
    return projects


async def _fill(
    conn: asyncpg.Connection, rng: random.Random, projects: list[dict]
) -> None:
    """Tasks, cards and the block mass — the part whose size matters."""
    card_rows: list[tuple] = []
    all_convs: list[uuid.UUID] = []
    question_convs: set[uuid.UUID] = set()
    task_number = 0

    for project in projects:
        pid = project["id"]
        is_main = project["index"] == 0
        n_tasks = MAIN_TASKS if is_main else OTHER_TASKS
        n_open = MAIN_OPEN if is_main else OTHER_OPEN
        rooms = project["rooms"]

        for t in range(n_tasks):
            task_number += 1
            tid = uuid.uuid4()
            room = rooms[t % len(rooms)]
            is_open = t < n_open
            owner = VIEWER if t % 5 else OTHERS[t % len(OTHERS)]
            created = now() - timedelta(hours=t % 720)
            await conn.execute(
                """insert into tasks (id, project_id, room_id, title, status,
                       owner_handle, created_by, created_at, updated_at,
                       accepted_at, closed_at, conclusion,
                       title_source, title_version, title_calibrated,
                       last_check_detail, reporter_handle, number)
                   values ($1,$2,$3,$4,$5,$6,$7,$8,$8,$9,$10,$11,
                           'human',0,false,'',$12,$13)""",
                tid,
                pid,
                room,
                f"task {task_number}",
                "open" if is_open else "closed",
                owner,
                VIEWER,
                created,
                None if is_open else created + timedelta(hours=2),
                None if is_open else created + timedelta(hours=3),
                None if is_open else "done",
                VIEWER,
                t + 1,
            )
            all_convs.append(tid)
            if is_open:
                # An open task usually sits behind a live card; a few are just
                # building so they do not address anyone at all.
                if t % 3 == 0:
                    card_rows.append(_card(uuid.uuid4(), None, tid, room, "pending"))
            elif t % 6 != 0:
                # Closed work still carries the card it was accepted on.
                card_rows.append(_card(uuid.uuid4(), None, tid, room, "accepted"))
            if t % 7 == 0:
                question_convs.add(tid)

        for room in rooms:
            all_convs.append(room)
            if rng.random() < 0.25:
                question_convs.add(room)
            # A handful of rooms carry room-level (task_id NULL) live cards.
            if rng.random() < 0.05:
                card_rows.append(_card(uuid.uuid4(), room, None, room, "pending_gate"))

    await conn.executemany(
        """insert into accept_cards (id, topic_id, task_id, reviewer_handle,
               routing_reason, status, note, created_at, updated_at, gate_output,
               rebase_count, nudge_state, delivered_task_ids, completes_task)
           values ($1,$2,$3,$4,$5,$6,$7,$8,$8,$9,$10,$11,$12,$13)""",
        card_rows,
    )
    print(f"cards: {len(card_rows)}", flush=True)

    conv_to_project = {
        row["id"]: row["project_id"]
        for row in await conn.fetch("select id, project_id from conversations")
    }

    hot = set(rng.sample(all_convs, min(HOT_CONVERSATIONS, len(all_convs))))
    rest = [c for c in all_convs if c not in hot]
    spread = max(0, N_BLOCKS - len(hot) * HOT_BLOCKS)
    per_other = spread // max(1, len(rest))

    total = 0
    for cid in all_convs:
        count = HOT_BLOCKS if cid in hot else per_other + rng.randint(-2, 2)
        if count <= 0:
            continue
        await _copy_blocks(
            conn, cid, conv_to_project[cid], count, cid in question_convs
        )
        total += count
    print(f"blocks: {total}", flush=True)
    await _boundary_blocks(conn, projects[0])


async def _boundary_blocks(conn: asyncpg.Connection, project: dict) -> None:
    """Give the archived and followed-up rooms the shape that would list them.

    An archived room gets a question addressed to ``VIEWER``: an archived room
    is read-only and nothing in it can be pushed forward, so its question must
    not reach the list. A followed-up room gets the agent's own question and
    then the agent's next line on the same conversation, which is the agent
    saying it no longer waits on anybody (#2046). The empty rooms are in the
    candidate set by being visible and get nothing else.
    """
    project_id = project["id"]
    base = now()
    for i, room in enumerate(project["boundary"]["archived room"]):
        await _one_block(
            conn,
            project_id,
            room,
            VIEWER,
            asked=True,
            said="这个频道归档之前问你的，归档之后就没人能动了。",
            at=base - timedelta(minutes=30 + i),
        )
    for i, room in enumerate(project["boundary"]["followed-up room"]):
        asked_at = base - timedelta(minutes=50 + i)
        await _one_block(
            conn,
            project_id,
            room,
            AGENT,
            asked=True,
            said="这一步按哪个方案走？",
            at=asked_at,
        )
        await _one_block(
            conn,
            project_id,
            room,
            AGENT,
            asked=False,
            said="不用回了，我按第一个做完了。",
            at=asked_at + timedelta(minutes=1),
        )
    print(
        f"boundary rooms: {EMPTY_ROOMS} empty, {ARCHIVED_ROOMS} archived with an"
        f" open question, {AGENT_FOLLOWUP_ROOMS} the asker walked away from",
        flush=True,
    )


async def _one_block(
    conn: asyncpg.Connection,
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    author: str,
    *,
    asked: bool,
    said: str,
    at: datetime,
) -> None:
    """One participant line, a question addressed to ``VIEWER`` when asked.

    ``author`` may be an agent handle: a question is answered by people, but it
    can be *asked* by either, and ``looks_like_agent_handle`` is what tells the
    two apart on the read side.
    """
    meta = json.dumps({"options": ["yes", "no"], "asked": [VIEWER]}) if asked else None
    await conn.execute(
        """insert into blocks (id, project_id, conversation_id, kind, author_type,
               author, content, reply_to, refs, created_at, updated_at, meta, seq)
           values ($1,$2,$3,'message','participant',$4,$5,null,$6,$7,$7,$8,$9)""",
        uuid.uuid4(),
        project_id,
        conversation_id,
        author,
        said,
        json.dumps([]),
        at,
        meta,
        1 if asked else 2,
    )


def _card(
    card_id: uuid.UUID,
    topic_id: uuid.UUID | None,
    task_id: uuid.UUID | None,
    place: uuid.UUID,
    status: str,
) -> tuple:
    created = now() - timedelta(minutes=7)
    return (
        card_id,
        topic_id if topic_id is not None else place,
        task_id,
        VIEWER,
        "reviewer",
        status,
        "note",
        created,
        "",
        0,
        "{}",
        "[]",
        True,
    )


async def _copy_blocks(
    conn: asyncpg.Connection,
    conversation_id: uuid.UUID,
    project_id: uuid.UUID,
    count: int,
    with_question: bool,
) -> None:
    base = now()
    records = []
    for i in range(count):
        created = base - timedelta(seconds=(count - i) * 7)
        is_question = with_question and i == count - 1
        meta = json.dumps({"options": ["yes", "no"]}) if is_question else None
        records.append(
            (
                uuid.uuid4(),
                project_id,
                conversation_id,
                "message",
                "participant",
                VIEWER,
                f"message {i} in this conversation, roughly the shape of a chat line",
                None,
                json.dumps([]),
                created,
                created,
                None,
                None,
                meta,
                i + 1,
            )
        )
    await conn.copy_records_to_table(
        "blocks",
        records=records,
        columns=[
            "id",
            "project_id",
            "conversation_id",
            "kind",
            "author_type",
            "author",
            "content",
            "reply_to",
            "refs",
            "created_at",
            "updated_at",
            "turn_id",
            "mime_type",
            "meta",
            "seq",
        ],
    )


async def _notifications(conn: asyncpg.Connection, projects: list[dict]) -> None:
    rows = []
    for project in projects[:3]:
        for i in range(4):
            rows.append(
                (
                    await conn.fetchval("select nextval('notification_seq')"),
                    "decision_request" if i % 2 == 0 else "change_alert",
                    False,
                    False,
                    False,
                    0,
                    now(),
                    now(),
                    VIEWER,
                    project["id"],
                    project["rooms"][i % len(project["rooms"])],
                    "light",
                    "heading",
                    "body",
                    None,
                    None,
                    json.dumps({}),
                    json.dumps({}),
                )
            )
    await conn.executemany(
        """insert into notification (id, type, read, is_aggregatable, finalized,
               version, created_at, updated_at, recipient_handle, project_id,
               topic_id, level, title, body, resolved_at, deleted_at,
               metadata, content)
           values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)""",
        rows,
    )


async def _report(conn: asyncpg.Connection) -> None:
    counts = {
        table: await conn.fetchval(f"select count(*) from {table}")
        for table in ("projects", "topics", "tasks", "blocks", "accept_cards")
    }
    print("row counts:", json.dumps(counts, indent=2), flush=True)


if __name__ == "__main__":
    main()
