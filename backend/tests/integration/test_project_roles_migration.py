"""What migration b34ba429e24d does to what is already there (#3830).

The rules, stated before it was written:

- an invitation already sent brings its invitee in as a member, the role every
  invitation meant until roles existed;
- nobody's role is rewritten: no role, permission or transfer row appears;
- run again after a partial run, it finishes instead of failing.
"""

import uuid

import pytest

from tests.integration.migration_replay import database_at, seed_room

BEFORE = "5d0e8b3c7a21"
AFTER = "b34ba429e24d"


@pytest.fixture
def db():
    with database_at(BEFORE) as replay:
        yield replay


def _invite(db, project) -> uuid.UUID:
    invitation = uuid.uuid4()
    db.execute(
        "INSERT INTO project_invitations (id, project_id, invitee_handle,"
        " inviter_handle, status, created_at, updated_at)"
        " VALUES ($1, $2, 'bob', 'alice', 'pending', now(), now())",
        invitation,
        project,
    )
    return invitation


def test_a_pending_invitation_brings_a_member_and_nothing_is_rewritten(db, tmp_path):
    project, _room = seed_room(db)
    invitation = _invite(db, project)

    db.upgrade(AFTER, {"WORKSPACE_ROOT": str(tmp_path)})

    assert (
        db.fetchval("SELECT role FROM project_invitations WHERE id = $1", invitation)
        == "member"
    )
    for table in ("project_roles", "project_role_grants", "project_transfers"):
        assert db.fetchval(f"SELECT count(*) FROM {table}") == 0


def test_a_run_that_stopped_after_its_column_completes(db, tmp_path):
    project, _room = seed_room(db)
    _invite(db, project)
    db.execute(
        "ALTER TABLE project_invitations ADD COLUMN role varchar(16)"
        " NOT NULL DEFAULT 'member'"
    )

    db.upgrade(AFTER, {"WORKSPACE_ROOT": str(tmp_path)})

    assert db.fetchval("SELECT count(*) FROM project_roles") == 0
