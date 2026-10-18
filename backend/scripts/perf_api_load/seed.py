"""Seed the API load harness's dataset: 50 projects / 500 channels / 2000 tasks / 100k messages.

The shape follows the tracker's request (2026-10-18, #1825 replacement): the
load harness measures ``GET /topics``, ``GET /topics/{id}/history``,
``GET /projects/{id}/tasks`` (whatever the task list route is), the
accept-card read, and the ``/llm/v1/messages`` proxy at 50/200 concurrent
callers. Row counts matter, not pretty data.

Everything is written straight through asyncpg (``COPY`` for blocks) and the
script TRUNCATEs the tables it owns, so it is re-runnable. It only ever talks
to the database named by ``PERF_DATABASE_URL``; run it against a throwaway
database, never a dev or test database.

    PERF_DATABASE_URL=postgresql://cheesex:cheesex@127.0.0.1:5433/cheese_perf \
        uv run --project backend python backend/scripts/perf_api_load/seed.py

The seeded viewer is ``perfviewer``: it owns every project, which is what lets
the harness mint one bearer token and read all of them.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg

DSN = os.environ.get(
    "PERF_DATABASE_URL", "postgresql://cheesex:cheesex@127.0.0.1:5433/cheese_perf"
)

VIEWER = os.environ.get("PERF_HANDLE", "perfviewer")
OTHERS = [f"perfuser{i}" for i in range(1, 13)]

# —— the tracker's shape ——————————————————————————————————————————————
N_PROJECTS = int(os.environ.get("PERF_PROJECTS", "50"))
#: The first project is dev-shaped: on dev one project holds ~170 rooms and
#: ~1,400 pieces of work, while the rest of the platform is smaller. A flat
#: 10-per-project seed would measure a sidebar that no real project has.
MAIN_ROOMS = int(os.environ.get("PERF_MAIN_ROOMS", "300"))
MAIN_TASKS = int(os.environ.get("PERF_MAIN_TASKS", "1000"))
TOTAL_ROOMS = int(os.environ.get("PERF_TOTAL_ROOMS", "500"))
TOTAL_TASKS = int(os.environ.get("PERF_TOTAL_TASKS", "2000"))
N_BLOCKS = int(os.environ.get("PERF_BLOCKS", "100000"))
#: How many conversations carry a long transcript, and how long each is. The
#: history endpoint reads the newest page from one conversation; making a few
#: rooms deep separates "the index is read" from "the room is scanned".
HOT_CONVERSATIONS = int(os.environ.get("PERF_HOT_CONVERSATIONS", "5"))
HOT_BLOCKS = int(os.environ.get("PERF_HOT_BLOCKS", "10000"))

SEED = 20261018


def now() -> datetime:
    return datetime.now(UTC)


async def main() -> None:
    rng = random.Random(SEED)
    conn = await asyncpg.connect(DSN)
    try:
        await _truncate(conn)
        team_id = await _team(conn)
        user_ids = await _users(conn)
        projects = await _projects(conn, team_id, rng)
        await _fill(conn, rng, projects)
        await _report(conn)
        print(f"viewer user id: {user_ids[VIEWER]}", flush=True)
    finally:
        await conn.close()


async def _truncate(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """truncate blocks, conversations, tasks, topics, threads, accept_cards,
                    projects, notification, team, "users" restart identity cascade"""
    )
    # The 1.0 login table is separate from the legacy ``users`` table above.
    await conn.execute('delete from "user" where username like $1', "perf%")


async def _team(conn: asyncpg.Connection) -> int:
    team_id = await conn.fetchval("select nextval('team_seq')")
    await conn.execute(
        """insert into team (id, name, handle, intro, description, avatar_id,
               created_at, updated_at, visibility, join_approval, plan_key)
           values ($1,'perf','perfteam','','',0,$2,$2,'public',true,'free')""",
        team_id,
        now(),
    )
    return team_id


async def _users(conn: asyncpg.Connection) -> dict[str, int]:
    """One row per handle in both user tables.

    ``users`` is the legacy profile table the rest of the seed still writes;
    ``user`` is what the bearer-token layer resolves a handle through. The
    harness needs the latter to have an integer id to put in the token.
    """
    handles = [VIEWER, *OTHERS]
    rows = [
        (h, h, "", json.dumps([]), json.dumps([]), uuid.uuid4(), now(), now())
        for h in handles
    ]
    await conn.executemany(
        """insert into users (handle, name, bio, interests, skills, id,
               created_at, updated_at)
           values ($1,$2,$3,$4,$5,$6,$7,$8)""",
        rows,
    )
    ids: dict[str, int] = {}
    for handle in handles:
        uid = await conn.fetchval(
            """insert into "user" (username, email, created_at, updated_at)
               values ($1, $2, $3, $3) returning id""",
            handle,
            f"{handle}@perf.invalid",
            now(),
        )
        ids[handle] = uid
    return ids


async def _projects(
    conn: asyncpg.Connection, team_id: int, rng: random.Random
) -> list[dict]:
    projects = []
    for i in range(N_PROJECTS):
        pid = uuid.uuid4()
        root = uuid.uuid4()
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
        rooms = [root]
        room_count = (
            MAIN_ROOMS
            if i == 0
            else max(1, (TOTAL_ROOMS - MAIN_ROOMS) // max(1, N_PROJECTS - 1))
        )
        for r in range(room_count):
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
            rooms.append(rid)
        projects.append({"id": pid, "root": root, "rooms": rooms, "index": i})
    return projects


async def _fill(
    conn: asyncpg.Connection, rng: random.Random, projects: list[dict]
) -> None:
    card_rows: list[tuple] = []
    all_convs: list[uuid.UUID] = []
    task_number = 0

    for project in projects:
        pid = project["id"]
        rooms = project["rooms"]
        task_count = (
            MAIN_TASKS
            if project["index"] == 0
            else max(1, (TOTAL_TASKS - MAIN_TASKS) // max(1, N_PROJECTS - 1))
        )
        for t in range(task_count):
            task_number += 1
            tid = uuid.uuid4()
            room = rooms[t % len(rooms)]
            is_open = t < task_count // 2
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
                if t % 3 == 0:
                    card_rows.append(_card(uuid.uuid4(), None, tid, room, "pending"))
            elif t % 6 != 0:
                card_rows.append(_card(uuid.uuid4(), None, tid, room, "accepted"))
        for room in rooms:
            all_convs.append(room)
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
        await _copy_blocks(conn, cid, conv_to_project[cid], count)
        total += count
    print(f"blocks: {total}", flush=True)


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
) -> None:
    base = now()
    records = []
    for i in range(count):
        created = base - timedelta(seconds=(count - i) * 7)
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
                None,
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


async def _report(conn: asyncpg.Connection) -> None:
    counts = {
        table: await conn.fetchval(f"select count(*) from {table}")
        for table in ("projects", "topics", "tasks", "blocks", "accept_cards")
    }
    print("row counts:", json.dumps(counts, indent=2), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
