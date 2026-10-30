"""What migration c4e8a1f27b93 does to roles already there (#4795).

The rules, stated before it was written:

- a project that had mentors gets one mentor role, and they all hold it, with
  what the old mentor role could do there;
- an invitation still waiting to bring a mentor brings that role;
- a built-in role's changed permissions stay changed;
- a project with no mentors gains no role;
- run again after a partial run, it finishes instead of failing.
"""

import uuid

import pytest

from tests.integration.migration_replay import database_at, seed_room

BEFORE = "e3f9a1c62b47"
AFTER = "c4e8a1f27b93"


@pytest.fixture
def db():
    with database_at(BEFORE) as replay:
        yield replay


def _seed(db) -> tuple[uuid.UUID, uuid.UUID]:
    project, _room = seed_room(db)
    db.execute(
        "INSERT INTO project_roles (id, project_id, user_handle, role, created_at,"
        " updated_at) VALUES (gen_random_uuid(), $1, 'teacher', 'mentor', now(), now()),"
        " (gen_random_uuid(), $1, 'bob', 'admin', now(), now())",
        project,
    )
    db.execute(
        "INSERT INTO project_role_grants (id, project_id, role, permissions,"
        " created_at, updated_at) VALUES"
        " (gen_random_uuid(), $1, 'mentor', '[\"speak\"]'::jsonb, now(), now()),"
        " (gen_random_uuid(), $1, 'member', '[\"speak\", \"comment\"]'::jsonb, now(), now())",
        project,
    )
    invitation = uuid.uuid4()
    db.execute(
        "INSERT INTO project_invitations (id, project_id, invitee_handle,"
        " inviter_handle, status, role, created_at, updated_at)"
        " VALUES ($1, $2, 'advisor', 'alice', 'pending', 'mentor', now(), now())",
        invitation,
        project,
    )
    return project, invitation


def test_mentors_hold_one_mentor_role_with_what_it_could_do(db, tmp_path):
    project, invitation = _seed(db)
    bare, _ = seed_room(db)

    db.upgrade(AFTER, {"WORKSPACE_ROOT": str(tmp_path)})

    mentor = db.fetchrow(
        "SELECT id, name, permissions FROM project_role_defs"
        " WHERE project_id = $1 AND template = 'mentor'",
        project,
    )
    assert mentor is not None and mentor["name"] is None
    assert mentor["permissions"] == '["speak"]'
    held = db.fetchrow(
        "SELECT role, role_id FROM project_roles WHERE user_handle = 'teacher'"
    )
    assert (held["role"], held["role_id"]) == ("custom", mentor["id"])
    assert (
        db.fetchval("SELECT role_id FROM project_invitations WHERE id = $1", invitation)
        == mentor["id"]
    )
    assert (
        db.fetchval("SELECT role FROM project_roles WHERE user_handle = 'bob'")
        == "admin"
    )
    assert (
        db.fetchval(
            "SELECT permissions FROM project_role_defs"
            " WHERE project_id = $1 AND builtin = 'member'",
            project,
        )
        == '["speak", "comment"]'
    )
    assert (
        db.fetchval(
            "SELECT count(*) FROM project_role_defs WHERE project_id = $1", bare
        )
        == 0
    )


def test_a_run_that_stopped_after_its_table_completes(db, tmp_path):
    project, _invitation = _seed(db)
    db.execute(
        "CREATE TABLE project_role_defs (id uuid PRIMARY KEY, project_id uuid NOT NULL,"
        " builtin varchar(16), name varchar(40), template varchar(16),"
        " permissions jsonb NOT NULL DEFAULT '[]'::jsonb,"
        " created_at timestamptz NOT NULL DEFAULT now(),"
        " updated_at timestamptz NOT NULL DEFAULT now(),"
        " CONSTRAINT uq_project_role_def_builtin UNIQUE (project_id, builtin),"
        " CONSTRAINT uq_project_role_def_name UNIQUE (project_id, name))"
    )

    db.upgrade(AFTER, {"WORKSPACE_ROOT": str(tmp_path)})

    assert (
        db.fetchval(
            "SELECT count(*) FROM project_role_defs WHERE project_id = $1 AND template = 'mentor'",
            project,
        )
        == 1
    )
