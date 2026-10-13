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
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg

DSN = os.environ.get(
    "PERF_DATABASE_URL", "postgresql://cheesex:cheesex@127.0.0.1:5433/cheese_perf"
)

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


def now() -> datetime:
    return datetime.now(UTC)


def main() -> None:
    asyncio.run(_main())


async def _main() -> None:
    rng = random.Random(SEED)
    conn = await asyncpg.connect(DSN)
    try:
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
        projects.append({"id": pid, "root": root, "rooms": rooms_list, "index": i})
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
                       accepted_at, closed_at, conclusion, started_at,
                       title_source, title_version, title_calibrated,
                       last_check_detail, reporter_handle, number)
                   values ($1,$2,$3,$4,$5,$6,$7,$8,$8,$9,$10,$11,$12,
                           'human',0,false,'',$13,$14)""",
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
                created + timedelta(minutes=5),
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
