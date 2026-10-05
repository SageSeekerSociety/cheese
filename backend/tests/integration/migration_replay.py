"""Replaying a data migration on the schema of its own revision.

A migration runs on the schema of the revision it belongs to, not on today's, so
a test of one builds a database at the revision before it, seeds the scenario
with raw SQL in that revision's column names, upgrades to the migration, and
reads the result back as data. The seeding helpers below speak the shape the
``topic_id`` / ``task_id`` columns had before conversations replaced them; use
them only for revisions older than that change.
"""

import asyncio
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import asyncpg

from tests.conftest import _PG_BASE, _admin_recreate_db
from tests.integration.test_task_conversation_migration import _alembic

_BACKEND = Path(__file__).resolve().parents[2]


class ReplayDatabase:
    """A database being walked through the revision history."""

    def __init__(self, name: str) -> None:
        self.url = f"{_PG_BASE}/{name}"
        self._dsn = self.url.replace("+asyncpg", "")

    def upgrade(self, revision: str) -> None:
        _alembic(self.url, revision)

    def downgrade(self, revision: str) -> None:
        """Step back to ``revision`` so the migration above it can run again."""
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", revision],
            cwd=_BACKEND,
            env={**os.environ, "DATABASE_URL": self.url},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    def run(self, work: Callable[[asyncpg.Connection], Awaitable[Any]]) -> Any:
        async def go() -> Any:
            conn = await asyncpg.connect(self._dsn)
            try:
                return await work(conn)
            finally:
                await conn.close()

        return asyncio.run(go())

    def execute(self, sql: str, *args: Any) -> None:
        self.run(lambda conn: conn.execute(sql, *args))

    def fetch(self, sql: str, *args: Any) -> list[asyncpg.Record]:
        return self.run(lambda conn: conn.fetch(sql, *args))

    def fetchrow(self, sql: str, *args: Any) -> asyncpg.Record | None:
        return self.run(lambda conn: conn.fetchrow(sql, *args))

    def fetchval(self, sql: str, *args: Any) -> Any:
        return self.run(lambda conn: conn.fetchval(sql, *args))


@contextmanager
def database_at(revision: str) -> Iterator[ReplayDatabase]:
    """A fresh database upgraded to ``revision``, dropped on exit."""
    name = "migration_replay_" + uuid.uuid4().hex[:12]
    asyncio.run(_admin_recreate_db(name))
    db = ReplayDatabase(name)
    try:
        db.upgrade(revision)
        yield db
    finally:

        async def drop() -> None:
            conn = await asyncpg.connect(_PG_BASE.replace("+asyncpg", "") + "/postgres")
            try:
                await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            finally:
                await conn.close()

        asyncio.run(drop())


def seed_room(db: ReplayDatabase, *, team: bool = True) -> tuple[uuid.UUID, uuid.UUID]:
    """A project and one room in it. Returns ``(project_id, room_id)``.

    ``team`` is False for revisions before a project belonged to a team."""
    project, room = uuid.uuid4(), uuid.uuid4()
    team_id = 9_000_001 + uuid.uuid4().int % 1_000_000

    async def work(conn: asyncpg.Connection) -> None:
        if team:
            # A shared team carries a handle, once the column exists.
            has_handle = await conn.fetchval(
                "SELECT count(*) FROM information_schema.columns"
                " WHERE table_name = 'team' AND column_name = 'handle'"
            )
            await conn.execute(
                "INSERT INTO team (id, name, intro, description, avatar_id,"
                " created_at, updated_at"
                + (", handle" if has_handle else "")
                + ") VALUES ($1, 't', '', '', 0, now(), now()"
                + (", $2" if has_handle else "")
                + ")",
                team_id,
                *([f"team-{team_id}"] if has_handle else []),
            )
            await conn.execute(
                "INSERT INTO projects (id, name, ai_mode, settings, team_id,"
                " created_at, updated_at)"
                " VALUES ($1, 'p', 'auto', '{}', $2, now(), now())",
                project,
                team_id,
            )
        else:
            await conn.execute(
                "INSERT INTO projects (id, name, ai_mode, settings, created_at,"
                " updated_at) VALUES ($1, 'p', 'auto', '{}', now(), now())",
                project,
            )
        await conn.execute(
            "INSERT INTO topics (id, project_id, title, kind, status, created_at,"
            " updated_at) VALUES ($1, $2, 'Room', 'topic', 'active', now(), now())",
            room,
            project,
        )

    db.run(work)
    return project, room


def seed_task(db: ReplayDatabase, project: uuid.UUID, room: uuid.UUID) -> uuid.UUID:
    task = uuid.uuid4()
    db.execute(
        "INSERT INTO tasks (id, project_id, room_id, title, status, created_at,"
        " updated_at) VALUES ($1, $2, $3, 'work', 'open', now(), now())",
        task,
        project,
        room,
    )
    return task


def seed_turn(
    db: ReplayDatabase,
    room: uuid.UUID,
    *,
    started: datetime,
    stopped: datetime | None = None,
    delivered: bool = True,
    author: str = "alice",
    content: str = "hello",
    resendable: bool = True,
    seat: str,
    task: uuid.UUID | None = None,
) -> uuid.UUID:
    """One turn on ``room``'s seat (``agent_handle``)."""
    turn = uuid.uuid4()
    db.execute(
        "INSERT INTO agent_turns (id, topic_id, task_id, continuation_id, author,"
        " content, is_resume, resendable, started_at, delivered_at, stopped_at,"
        " agent_handle) VALUES ($1, $2, $3, $1, $4, $5, false, $6, $7, $8, $9, $10)",
        turn,
        room,
        task,
        author,
        content,
        resendable,
        started,
        started if delivered else None,
        stopped,
        seat,
    )
    return turn


def seed_block(
    db: ReplayDatabase,
    project: uuid.UUID,
    room: uuid.UUID,
    *,
    turn: uuid.UUID | None,
    at: datetime,
    author: str,
    meta: dict | None = None,
) -> uuid.UUID:
    """A block in ``room`` written at ``at``, under ``turn`` when it names one.
    With ``meta`` it is a platform event, otherwise a message."""
    block = uuid.uuid4()
    db.execute(
        "INSERT INTO blocks (id, project_id, topic_id, kind, author_type, author,"
        " content, refs, meta, turn_id, created_at, updated_at)"
        " VALUES ($1, $2, $3, $4, $5, $6, 'working on it', '[]', $7::json, $8,"
        " $9, $9)",
        block,
        project,
        room,
        "event" if meta else "message",
        "platform" if meta else "participant",
        author,
        json.dumps(meta) if meta else None,
        turn,
        at,
    )
    return block


def stopped_at(db: ReplayDatabase, turn: uuid.UUID) -> datetime | None:
    return db.fetchval("SELECT stopped_at FROM agent_turns WHERE id = $1", turn)


def room_is_busy(db: ReplayDatabase, room: uuid.UUID) -> bool:
    """A room is busy exactly while one of its turns has no end."""
    return bool(
        db.fetchval(
            "SELECT count(*) FROM agent_turns"
            " WHERE topic_id = $1 AND stopped_at IS NULL",
            room,
        )
    )


def stand_in_handle(room: uuid.UUID) -> str:
    """The handle old code derived from a **room**. The function that minted it
    is gone, so the string stored in old rows is spelled out here."""
    return f"cheese-{room.hex[:12]}"


def seat_handle(instance: uuid.UUID) -> str:
    """The handle a saved agent sits on rosters as: its id's first 12 hex digits."""
    return f"cheese-{instance.hex[:12]}"


def seed_agent_project(
    db: ReplayDatabase, *, default_agent: bool = True
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID | None]:
    """A project with its overview room, and its default agent unless
    ``default_agent`` is False. Returns ``(project_id, overview_id, agent_id)``.
    For revisions before a project belonged to a team."""
    project, root = seed_room(db, team=False)
    db.execute("UPDATE projects SET root_topic_id = $1 WHERE id = $2", root, project)
    agent = seed_agent(db, project, "cheese") if default_agent else None
    if agent is not None:
        db.execute(
            "UPDATE projects SET default_agent_instance_id = $1 WHERE id = $2",
            agent,
            project,
        )
    return project, root, agent


def seed_agent(db: ReplayDatabase, project: uuid.UUID, handle: str) -> uuid.UUID:
    agent = uuid.uuid4()
    db.execute(
        "INSERT INTO agent_instances (id, project_id, handle, display_name,"
        " configuration, created_at, updated_at)"
        " VALUES ($1, $2, $3, $3, '{}', now(), now())",
        agent,
        project,
        handle,
    )
    return agent


def seat_agent(db: ReplayDatabase, room: uuid.UUID, handle: str) -> None:
    """Seat ``handle`` on ``room``'s roster as an agent: a user row, the
    execution binding that makes a user an agent, and the roster row."""

    async def work(conn: asyncpg.Connection) -> None:
        user = await conn.fetchval('SELECT id FROM "user" WHERE username = $1', handle)
        if user is None:
            user = await conn.fetchval(
                'INSERT INTO "user" (username, email, created_at, updated_at)'
                " VALUES ($1::varchar, $1::varchar || '@agent.cheese.local',"
                " now(), now())"
                " RETURNING id",
                handle,
            )
            await conn.execute(
                "INSERT INTO agent_bindings (id, user_id, kind, created_at,"
                " updated_at) VALUES ($1, $2, 'platform', now(), now())",
                uuid.uuid4(),
                user,
            )
        await conn.execute(
            "INSERT INTO topic_memberships (id, topic_id, member_handle, role,"
            " created_at, updated_at) VALUES ($1, $2, $3, 'member', now(), now())",
            uuid.uuid4(),
            room,
            handle,
        )

    db.run(work)


def roster(db: ReplayDatabase, room: uuid.UUID) -> set[str]:
    return {
        row["member_handle"]
        for row in db.fetch(
            "SELECT member_handle FROM topic_memberships WHERE topic_id = $1", room
        )
    }


def block_author(db: ReplayDatabase, block: uuid.UUID) -> str:
    return db.fetchval("SELECT author FROM blocks WHERE id = $1", block)


def seed_extra_room(db: ReplayDatabase, project: uuid.UUID) -> uuid.UUID:
    """Another room in ``project``."""
    room = uuid.uuid4()
    db.execute(
        "INSERT INTO topics (id, project_id, title, kind, status, created_at,"
        " updated_at) VALUES ($1, $2, 'Old room', 'topic', 'active', now(), now())",
        room,
        project,
    )
    return room
