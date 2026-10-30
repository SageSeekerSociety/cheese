"""Projects have their own roles; a channel may let only some speak

Revision ID: c4e8a1f27b93
Revises: b7d41e9c05af
Create Date: 2026-10-25

A project's roles are now admin and member, built in, plus any of its own
(#4795). ``project_role_defs`` holds both a built-in role's changed
permissions (copied from ``project_role_grants``, which a later migration
drops once no serving release reads it) and the project's own roles.
Mentor stops being a role every project has: it becomes a template, and a
project that had mentors, a mentor invitation still waiting, or a changed
mentor role gets one role made from it, unnamed so each reader sees the
template's name, holding what the old mentor role could do there. Everyone who
held it, and every invitation waiting to give it, points at that role.

A channel records which roles may speak in its main line
(``topics.speaker_roles``, none meaning everyone) and whether everyone may still
answer in a 支线 (``topics.thread_replies_open``).

One transaction, each step safe to repeat (the lesson of 5d0e8b3c7a21).
"""

from collections.abc import Sequence

from migration_helpers import with_lock_retries

from alembic import op

revision: str = "c4e8a1f27b93"
down_revision: str | Sequence[str] | None = "b7d41e9c05af"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: What the old mentor role could do where a project had not changed it.
_MENTOR = '["ask_agent", "comment", "speak"]'


def upgrade() -> None:
    with_lock_retries(
        "projects, project_roles, project_invitations, project_role_grants, topics"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS project_role_defs (
            id uuid PRIMARY KEY,
            project_id uuid NOT NULL
                CONSTRAINT fk_project_role_defs_project_id_projects
                REFERENCES projects (id) ON DELETE CASCADE,
            builtin varchar(16),
            name varchar(40),
            template varchar(16),
            permissions jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_project_role_def_builtin UNIQUE (project_id, builtin),
            CONSTRAINT uq_project_role_def_name UNIQUE (project_id, name),
            CONSTRAINT ck_project_role_defs_builtin_or_named CHECK (
                (builtin IS NOT NULL AND name IS NULL AND template IS NULL)
                OR (builtin IS NULL AND (name IS NOT NULL OR template IS NOT NULL))
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_project_role_defs_project_id"
        " ON project_role_defs (project_id)"
    )
    op.execute("ALTER TABLE project_roles ADD COLUMN IF NOT EXISTS role_id uuid")
    op.execute("ALTER TABLE project_invitations ADD COLUMN IF NOT EXISTS role_id uuid")
    # Someone holding a role keeps it from being deleted; an answered
    # invitation is a record and lets go of it.
    for table, on_delete in (
        ("project_roles", "RESTRICT"),
        ("project_invitations", "SET NULL"),
    ):
        op.execute(
            f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS"
            f" fk_{table}_role_id_project_role_defs"
        )
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT fk_{table}_role_id_project_role_defs"
            " FOREIGN KEY (role_id) REFERENCES project_role_defs (id)"
            f" ON DELETE {on_delete} NOT VALID"
        )
        # migration-safety: allow validate-outside-autocommit — both tables hold a few hundred rows; the whole migration takes well under a second
        op.execute(
            f"ALTER TABLE {table} VALIDATE CONSTRAINT"
            f" fk_{table}_role_id_project_role_defs"
        )
    # migration-safety: allow index-not-concurrent — project_roles holds a few hundred rows
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_project_roles_role_id ON project_roles (role_id)"
    )
    # migration-safety: allow index-not-concurrent — project_invitations holds a few hundred rows
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_project_invitations_role_id"
        " ON project_invitations (role_id)"
    )
    op.execute(
        "ALTER TABLE topics ADD COLUMN IF NOT EXISTS speaker_roles jsonb,"
        " ADD COLUMN IF NOT EXISTS thread_replies_open boolean NOT NULL DEFAULT true"
    )

    grants_left = (
        op.get_bind()
        .exec_driver_sql("SELECT to_regclass('project_role_grants') IS NOT NULL")
        .scalar()
    )
    if grants_left:
        # A built-in role a project changed keeps its change.
        op.execute(
            """
            INSERT INTO project_role_defs (id, project_id, builtin, permissions)
            SELECT gen_random_uuid(), project_id, role, permissions
            FROM project_role_grants
            WHERE role IN ('admin', 'member')
            ON CONFLICT (project_id, builtin) DO NOTHING
            """
        )
    mentor_grants = (
        "(SELECT g.permissions FROM project_role_grants g"
        " WHERE g.project_id = p.project_id AND g.role = 'mentor')"
        if grants_left
        else "NULL"
    )
    mentor_projects = (
        "SELECT project_id FROM project_roles WHERE role = 'mentor'"
        " UNION SELECT project_id FROM project_invitations"
        " WHERE role = 'mentor' AND status = 'pending'"
    )
    if grants_left:
        mentor_projects += (
            " UNION SELECT project_id FROM project_role_grants WHERE role = 'mentor'"
        )
    op.execute(
        f"""
        INSERT INTO project_role_defs (id, project_id, template, permissions)
        SELECT gen_random_uuid(), p.project_id, 'mentor',
               COALESCE({mentor_grants}, '{_MENTOR}'::jsonb)
        FROM ({mentor_projects}) AS p
        WHERE NOT EXISTS (
            SELECT 1 FROM project_role_defs d
            WHERE d.project_id = p.project_id AND d.template = 'mentor'
        )
        """
    )
    op.execute(
        """
        UPDATE project_roles r
        SET role = 'custom', role_id = d.id
        FROM project_role_defs d
        WHERE r.role = 'mentor' AND d.project_id = r.project_id
          AND d.template = 'mentor'
        """
    )
    op.execute(
        """
        UPDATE project_invitations i
        SET role = 'custom', role_id = d.id
        FROM project_role_defs d
        WHERE i.role = 'mentor' AND d.project_id = i.project_id
          AND d.template = 'mentor'
        """
    )
    # An answered invitation that brought a mentor is a record, not a promise:
    # it keeps saying what it was, against a role that may later go.
    op.execute(
        "UPDATE project_invitations SET role = 'member'"
        " WHERE role = 'mentor' AND role_id IS NULL"
    )
    # Member is what someone holds with no row.
    op.execute("DELETE FROM project_roles WHERE role = 'member'")
    op.execute(
        "ALTER TABLE project_roles DROP CONSTRAINT IF EXISTS"
        " ck_project_roles_custom_has_role_id"
    )
    op.execute(
        "ALTER TABLE project_roles ADD CONSTRAINT ck_project_roles_custom_has_role_id"
        " CHECK ((role = 'custom') = (role_id IS NOT NULL)) NOT VALID"
    )
    # migration-safety: allow validate-outside-autocommit — project_roles holds a few hundred rows
    op.execute(
        "ALTER TABLE project_roles VALIDATE CONSTRAINT ck_project_roles_custom_has_role_id"
    )
    # project_role_grants is no longer read; the release still serving reads
    # it, so it goes in a later migration.


def downgrade() -> None:
    raise NotImplementedError("Projects' own roles cannot be folded back into three.")
