"""Existing projects get slugs and existing things get numbers (migration
5a00f11b4537).

The rules, as stated before the migration was written:

- every project gets a slug of its own;
- tasks, documents of the project's own and channels are numbered from 1 within
  their project, in the order they were made;
- a private chat, a task's document and the project overview get no number;
- the next one made continues after the last number given.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from tests.integration.migration_replay import (
    ReplayDatabase,
    database_at,
    seed_room,
)

BEFORE = "a4d8e2f61c07"
AFTER = "5a00f11b4537"


class World:
    def __init__(self, db: ReplayDatabase) -> None:
        self.db = db
        self.project, self.first_room = seed_room(db)
        self.other_project, self.other_room = seed_room(db)
        self.ids: dict[str, uuid.UUID] = {}

    def _at(self, minutes: int) -> datetime:
        return datetime(2026, 1, 1, 0, minutes, tzinfo=UTC)

    def topic(self, name: str, minutes: int, *, private: bool = False) -> None:
        self.ids[name] = uuid.uuid4()
        self.db.execute(
            "INSERT INTO topics (id, project_id, title, kind, status, is_private,"
            " created_at, updated_at)"
            " VALUES ($1, $2, $3, 'topic', 'active', $4, $5, now())",
            self.ids[name],
            self.project,
            name,
            private,
            self._at(minutes),
        )

    def task(self, name: str, minutes: int, *, document: str | None = None) -> None:
        self.ids[name] = uuid.uuid4()
        self.db.execute(
            "INSERT INTO tasks (id, project_id, room_id, title, status, document_id,"
            " created_at, updated_at)"
            " VALUES ($1, $2, $3, $4, 'open', $5, $6, now())",
            self.ids[name],
            self.project,
            self.first_room,
            name,
            self.ids[document] if document else None,
            self._at(minutes),
        )

    def document(self, name: str, minutes: int) -> None:
        self.ids[name] = uuid.uuid4()
        self.db.execute(
            "INSERT INTO documents (id, project_id, title, content, version, author,"
            " created_at, updated_at)"
            " VALUES ($1, $2, $3, '', 0, 'alice', $4, now())",
            self.ids[name],
            self.project,
            name,
            self._at(minutes),
        )

    def number(self, table: str, name: str) -> int | None:
        return self.db.fetchval(
            f"SELECT number FROM {table} WHERE id = $1",  # noqa: S608
            self.ids[name],
        )


@pytest.fixture(scope="module")
def migrated() -> Iterator[World]:
    with database_at(BEFORE) as db:
        world = World(db)
        # The rooms seed_room made are older than anything below.
        db.execute(
            "UPDATE topics SET created_at = '2025-01-01'::timestamptz WHERE id = ANY($1)",
            [world.first_room, world.other_room],
        )
        world.topic("design", 5)
        world.topic("dm", 6, private=True)
        world.document("task-doc", 1)
        world.document("overview", 2)
        world.document("pricing", 3)
        world.document("notes", 4)
        world.task("later", 9)
        world.task("earlier", 8, document="task-doc")
        db.execute(
            "UPDATE projects SET overview_document_id = $1 WHERE id = $2",
            world.ids["overview"],
            world.project,
        )
        db.upgrade(AFTER)
        yield world


def test_every_project_gets_a_slug_of_its_own(migrated):
    slugs = migrated.db.fetch(
        "SELECT slug FROM projects WHERE id = ANY($1)",
        [migrated.project, migrated.other_project],
    )
    values = [row["slug"] for row in slugs]
    assert len(set(values)) == 2
    assert all(len(v) == 8 and v.isalnum() and v == v.lower() for v in values)


def test_things_are_numbered_by_kind_in_the_order_they_were_made(migrated):
    assert migrated.number("tasks", "earlier") == 1
    assert migrated.number("tasks", "later") == 2
    assert migrated.number("documents", "pricing") == 1
    assert migrated.number("documents", "notes") == 2
    first = migrated.db.fetchval(
        "SELECT number FROM topics WHERE id = $1", migrated.first_room
    )
    assert first == 1
    assert migrated.number("topics", "design") == 2
    other = migrated.db.fetchval(
        "SELECT number FROM topics WHERE id = $1", migrated.other_room
    )
    assert other == 1


def test_private_chats_task_documents_and_the_overview_get_no_number(migrated):
    assert migrated.number("topics", "dm") is None
    assert migrated.number("documents", "task-doc") is None
    assert migrated.number("documents", "overview") is None


def test_the_counters_continue_after_the_last_number(migrated):
    counters = {
        row["kind"]: row["last_number"]
        for row in migrated.db.fetch(
            "SELECT kind, last_number FROM project_counters WHERE project_id = $1",
            migrated.project,
        )
    }
    assert counters == {"tasks": 2, "docs": 2, "channels": 2}
