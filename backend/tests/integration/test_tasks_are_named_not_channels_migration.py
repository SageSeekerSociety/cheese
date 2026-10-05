"""The platform names tasks, not channels (migration 0058abcb379d).

The rules, as stated before the migration was written:

- a task never named is still unnamed afterwards, and is called 「新任务」
  rather than the room placeholder it was opened with; a named task keeps its
  title;
- a project that chose to name by hand still does;
- a channel no longer carries any naming state.
"""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.integration.migration_replay import ReplayDatabase, database_at
from tests.integration.test_rooms_become_tasks_migration import (
    AFTER as ROOMS_BECAME_TASKS,
)
from tests.integration.test_rooms_become_tasks_migration import BEFORE, World

AFTER = "0058abcb379d"


@pytest.fixture(scope="module")
def migrated(tmp_path_factory) -> Iterator[tuple[World, ReplayDatabase]]:
    workspace: Path = tmp_path_factory.mktemp("workspace")
    with database_at(BEFORE) as db:
        world = World(db)
        world.seed()
        world.files(workspace)
        db.upgrade(ROOMS_BECAME_TASKS, env={"WORKSPACE_ROOT": str(workspace)})
        db.execute(
            "UPDATE tasks SET title = '新话题', title_source = 'placeholder'"
            " WHERE id = $1",
            world.rooms["solo"],
        )
        db.execute(
            "UPDATE tasks SET title = '数据迁移', title_source = 'human' WHERE id = $1",
            world.rooms["pair"],
        )
        db.execute(
            "UPDATE projects SET settings = $2::json WHERE id = $1",
            world.project,
            json.dumps({"topic_naming": "manual", "other": 1}),
        )
        db.upgrade(AFTER)
        yield world, db


def test_an_unnamed_task_stays_unnamed_under_its_own_word(migrated):
    world, db = migrated
    solo = db.fetchrow(
        "SELECT title, title_source FROM tasks WHERE id = $1", world.rooms["solo"]
    )
    assert (solo["title"], solo["title_source"]) == ("新任务", "placeholder")
    pair = db.fetchrow(
        "SELECT title, title_source FROM tasks WHERE id = $1", world.rooms["pair"]
    )
    assert (pair["title"], pair["title_source"]) == ("数据迁移", "human")


def test_a_project_naming_by_hand_still_does(migrated):
    world, db = migrated
    settings = json.loads(
        db.fetchval("SELECT settings::text FROM projects WHERE id = $1", world.project)
    )
    assert settings == {"task_naming": "manual", "other": 1}


def test_a_channel_carries_no_naming_state(migrated):
    _, db = migrated
    columns = {
        row["column_name"]
        for row in db.fetch(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'topics'"
        )
    }
    assert not columns & {
        "title_source",
        "title_version",
        "title_checked_at",
        "title_calibrated",
    }
    assert db.fetchval("SELECT to_regclass('topic_titles')") is None
