"""add project_membership table

Revision ID: add_proj_member_001
Revises: add_passkey_001
Create Date: 2026-03-04
"""

from alembic import op
import sqlalchemy as sa


revision = "add_proj_member_001"
down_revision = "add_passkey_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS project_membership_seq")
    op.create_table(
        "project_membership",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Sequence("project_membership_seq"),
            primary_key=True,
            server_default=sa.text("nextval('project_membership_seq')"),
        ),
        sa.Column("project_id", sa.BigInteger(), sa.ForeignKey("project.id"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.SmallInteger(), nullable=False),
        sa.Column("notes", sa.String(500), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_project_membership_project_id",
        "project_membership",
        ["project_id"],
    )
    op.create_index(
        "uq_project_membership_project_user",
        "project_membership",
        ["project_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_project_membership_project_user", table_name="project_membership")
    op.drop_index("ix_project_membership_project_id", table_name="project_membership")
    op.drop_table("project_membership")
    op.execute("DROP SEQUENCE IF EXISTS project_membership_seq")
