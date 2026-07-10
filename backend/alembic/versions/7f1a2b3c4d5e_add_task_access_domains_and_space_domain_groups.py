"""Add task access control and space domain groups.

Revision ID: 7f1a2b3c4d5e
Revises: 5a3b7c9d1e2f
Create Date: 2026-05-11
"""

import sqlalchemy as sa
from alembic import op

revision = "7f1a2b3c4d5e"
down_revision = "5a3b7c9d1e2f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.schema.CreateSequence(sa.Sequence("space_domain_group_seq")))
    op.execute(sa.schema.CreateSequence(sa.Sequence("space_domain_group_domain_seq")))
    op.execute(sa.schema.CreateSequence(sa.Sequence("task_access_domain_seq")))

    op.create_table(
        "space_domain_group",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Sequence("space_domain_group_seq"),
            nullable=False,
        ),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_space_domain_group_space_id",
        "space_domain_group",
        ["space_id"],
    )
    op.create_index(
        "ix_space_domain_group_name",
        "space_domain_group",
        ["name"],
    )

    op.create_table(
        "space_domain_group_domain",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Sequence("space_domain_group_domain_seq"),
            nullable=False,
        ),
        sa.Column("group_id", sa.BigInteger(), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_space_domain_group_domain_group_id",
        "space_domain_group_domain",
        ["group_id"],
    )
    op.create_index(
        "ix_space_domain_group_domain_domain",
        "space_domain_group_domain",
        ["domain"],
    )

    op.add_column(
        "task",
        sa.Column(
            "access_control_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.create_table(
        "task_access_domain",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Sequence("task_access_domain_seq"),
            nullable=False,
        ),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_task_access_domain_task_id",
        "task_access_domain",
        ["task_id"],
    )
    op.create_index(
        "ix_task_access_domain_domain",
        "task_access_domain",
        ["domain"],
    )

    op.add_column(
        "user",
        sa.Column("email_domain", sa.String(), nullable=True),
    )
    op.execute(
        'UPDATE "user" '
        "SET email_domain = lower(split_part(email, '@', 2)) "
        "WHERE email IS NOT NULL AND email <> '' AND email_domain IS NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_task_access_domain_domain", table_name="task_access_domain")
    op.drop_index("ix_task_access_domain_task_id", table_name="task_access_domain")
    op.drop_table("task_access_domain")

    op.drop_column("task", "access_control_enabled")

    op.drop_index(
        "ix_space_domain_group_domain_domain",
        table_name="space_domain_group_domain",
    )
    op.drop_index(
        "ix_space_domain_group_domain_group_id",
        table_name="space_domain_group_domain",
    )
    op.drop_table("space_domain_group_domain")

    op.drop_index("ix_space_domain_group_name", table_name="space_domain_group")
    op.drop_index("ix_space_domain_group_space_id", table_name="space_domain_group")
    op.drop_table("space_domain_group")

    op.drop_column("user", "email_domain")

    op.execute(sa.schema.DropSequence(sa.Sequence("task_access_domain_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("space_domain_group_domain_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("space_domain_group_seq")))
