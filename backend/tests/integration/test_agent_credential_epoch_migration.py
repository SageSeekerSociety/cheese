"""A project's credential generation moves out of settings (migration c46448bdc315).

The rules, as stated before the migration was written:

- a project that had revoked keeps the same generation, so the credentials it
  retired stay retired;
- the generation no longer lives in `settings`, so a writer that replaces that
  blob whole cannot put an old one back;
- a project that never revoked anything sits at 0 and carries nothing extra in
  its settings.
"""

import json
import uuid
from collections.abc import Iterator

import pytest

from tests.integration.migration_replay import ReplayDatabase, database_at, seed_room

BEFORE = "9f2b7c14a8e3"
AFTER = "c46448bdc315"


@pytest.fixture(scope="module")
def migrated() -> Iterator[tuple[ReplayDatabase, uuid.UUID, uuid.UUID]]:
    with database_at(BEFORE) as db:
        revoked, _ = seed_room(db)
        db.execute(
            "UPDATE projects SET settings = $1::json WHERE id = $2",
            json.dumps({"agent_credential_epoch": 3, "task_naming": "auto"}),
            revoked,
        )
        untouched, _ = seed_room(db)
        db.upgrade(AFTER)
        yield db, revoked, untouched


def _epoch(db: ReplayDatabase, project: uuid.UUID) -> int:
    return db.fetchval(
        "SELECT agent_credential_epoch FROM projects WHERE id = $1", project
    )


def _settings(db: ReplayDatabase, project: uuid.UUID) -> dict:
    return json.loads(
        db.fetchval("SELECT settings FROM projects WHERE id = $1", project)
    )


def test_a_revoked_generation_survives_in_the_column(migrated):
    db, revoked, _ = migrated
    assert _epoch(db, revoked) == 3


def test_the_generation_leaves_the_settings_blob(migrated):
    db, revoked, _ = migrated
    assert "agent_credential_epoch" not in _settings(db, revoked)


def test_the_rest_of_that_project_settings_is_untouched(migrated):
    db, revoked, _ = migrated
    assert _settings(db, revoked) == {"task_naming": "auto"}


def test_a_project_that_never_revoked_sits_at_zero(migrated):
    db, _, untouched = migrated
    assert _epoch(db, untouched) == 0
    assert _settings(db, untouched) == {}
