"""Projects have roles, a role's permissions, owner transfers and mentors

Revision ID: b34ba429e24d
Revises: 5d0e8b3c7a21
Create Date: 2026-10-16

A project's people now hold a role — admin, member or mentor — under its one
owner (#3830). Most people's role follows from how they are in the project and
is not stored; ``project_roles`` holds the ones that differ. A project may
change what a role may do (``project_role_grants``). The owner hands the
project over by an offer the recipient accepts (``project_transfers``). An
invitation names the role it brings (``project_invitations.role``). A task may
name mentors who join every claimer's project (``task_mentor``).

Nothing existing is rewritten: with no rows, everyone keeps the role they
would have held, which is what they could do before for all but the people a
task's publisher could read without being invited — and that access is what
#3830 takes away.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b34ba429e24d"
down_revision: str | Sequence[str] | None = "5d0e8b3c7a21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    # One transaction, each step safe to repeat (the lesson of 5d0e8b3c7a21).
    # New foreign keys lock `projects` and `task` as well.
    with_lock_retries("project_invitations, projects, task")
    op.execute(
        "ALTER TABLE project_invitations ADD COLUMN IF NOT EXISTS"
        " role varchar(16) NOT NULL DEFAULT 'member'"
    )
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "project_roles" not in tables:
        op.create_table(
            "project_roles",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column(
                "project_id",
                sa.Uuid(),
                sa.ForeignKey(
                    "projects.id",
                    ondelete="CASCADE",
                    name="fk_project_roles_project_id_projects",
                ),
                nullable=False,
            ),
            sa.Column("user_handle", sa.String(64), nullable=False),
            sa.Column("role", sa.String(16), nullable=False),
            *_timestamps(),
            sa.UniqueConstraint("project_id", "user_handle", name="uq_project_role"),
        )
        op.create_index("ix_project_roles_project_id", "project_roles", ["project_id"])
        op.create_index(
            "ix_project_roles_user_handle", "project_roles", ["user_handle"]
        )
    if "project_role_grants" not in tables:
        op.create_table(
            "project_role_grants",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column(
                "project_id",
                sa.Uuid(),
                sa.ForeignKey(
                    "projects.id",
                    ondelete="CASCADE",
                    name="fk_project_role_grants_project_id_projects",
                ),
                nullable=False,
            ),
            sa.Column("role", sa.String(16), nullable=False),
            sa.Column("permissions", postgresql.JSONB(), nullable=False),
            *_timestamps(),
            sa.UniqueConstraint("project_id", "role", name="uq_project_role_grant"),
        )
        op.create_index(
            "ix_project_role_grants_project_id", "project_role_grants", ["project_id"]
        )
    if "project_transfers" not in tables:
        op.create_table(
            "project_transfers",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column(
                "project_id",
                sa.Uuid(),
                sa.ForeignKey(
                    "projects.id",
                    ondelete="CASCADE",
                    name="fk_project_transfers_project_id_projects",
                ),
                nullable=False,
            ),
            sa.Column("from_handle", sa.String(64), nullable=False),
            sa.Column("to_handle", sa.String(64), nullable=False),
            sa.Column("status", sa.String(16), nullable=False),
            sa.CheckConstraint(
                "status IN ('pending', 'accepted', 'declined', 'revoked')",
                name="ck_project_transfers_status",
            ),
            sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
            *_timestamps(),
        )
        op.create_index(
            "ix_project_transfers_project_id", "project_transfers", ["project_id"]
        )
        op.create_index(
            "ix_project_transfers_to_handle", "project_transfers", ["to_handle"]
        )
        op.create_index(
            "uq_project_transfer_pending",
            "project_transfers",
            ["project_id"],
            unique=True,
            postgresql_where=sa.text("status = 'pending'"),
        )
    if "task_mentor" not in tables:
        op.create_table(
            "task_mentor",
            sa.Column(
                "task_id",
                sa.BigInteger(),
                sa.ForeignKey(
                    "task.id", ondelete="CASCADE", name="fk_task_mentor_task_id_task"
                ),
                primary_key=True,
            ),
            sa.Column("user_handle", sa.String(64), primary_key=True),
        )


def downgrade() -> None:
    op.drop_table("task_mentor")
    op.drop_table("project_transfers")
    op.drop_table("project_role_grants")
    op.drop_table("project_roles")
    op.drop_column("project_invitations", "role")
