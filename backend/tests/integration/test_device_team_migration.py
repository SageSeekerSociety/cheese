"""Replay ownership backfill on the previous release's schema."""

import uuid

import pytest

from tests.integration.migration_replay import database_at, seed_room

PREVIOUS = "5d3e8a1f2b7c"
OWNERSHIP = "c9e25a7b38f1"


def seed_device(db, name):
    owner = db.fetchval(
        'INSERT INTO "user" (username, email, created_at, updated_at) VALUES ($1, $2, now(), now()) RETURNING id',
        f"owner-{name}",
        f"{name}@example.test",
    )
    db.execute(
        "INSERT INTO device (device_id, name, token, owner_user_id, created_at) VALUES ($1, $1, $1, $2, now())",
        name,
        owner,
    )
    return owner


def bind(db, device, team):
    db.execute(
        "INSERT INTO device_team (id, device_id, team_id, created_at, updated_at) VALUES ($1, $2, $3, now(), now())",
        uuid.uuid4(),
        device,
        team,
    )


def test_single_team_is_kept_and_unassigned_device_gets_enrollers_personal_team():
    with database_at(PREVIOUS) as db:
        project, _ = seed_room(db)
        team = db.fetchval("SELECT team_id FROM projects WHERE id = $1", project)
        unassigned_owner = seed_device(db, "unassigned")
        seed_device(db, "shared")
        bind(db, "shared", team)
        db.upgrade(OWNERSHIP)
        personal = db.fetchval(
            "SELECT id FROM team WHERE personal_owner_user_id = $1 AND deleted_at IS NULL",
            unassigned_owner,
        )
        assert personal is not None
        assert [
            (r["device_id"], r["team_id"])
            for r in db.fetch(
                "SELECT device_id, team_id FROM device ORDER BY device_id"
            )
        ] == [("shared", team), ("unassigned", personal)]
        assert (
            db.fetchval(
                "SELECT role FROM team_user_relation WHERE team_id = $1 AND user_id = $2 AND deleted_at IS NULL",
                personal,
                unassigned_owner,
            )
            == 0
        )
        # A previous backend still writing during the rolling deploy remains supported.
        db.execute(
            "INSERT INTO device (device_id, name, token, owner_user_id, created_at) VALUES ('old-writer', 'Old', 'old-writer', $1, now())",
            unassigned_owner,
        )
        assert (
            db.fetchval("SELECT team_id FROM device WHERE device_id = 'old-writer'")
            == personal
        )


def test_multiple_teams_abort_and_report_device_ids():
    with database_at(PREVIOUS) as db:
        seed_device(db, "ambiguous-device")
        for _ in range(2):
            project, _ = seed_room(db)
            bind(
                db,
                "ambiguous-device",
                db.fetchval("SELECT team_id FROM projects WHERE id = $1", project),
            )
        with pytest.raises(
            AssertionError, match="Devices assigned to multiple teams: ambiguous-device"
        ):
            db.upgrade(OWNERSHIP)
