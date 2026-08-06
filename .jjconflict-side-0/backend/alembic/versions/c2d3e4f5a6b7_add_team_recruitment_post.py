"""add team_recruitment_post table

Revision ID: c2d3e4f5a6b7
Revises: b1f2a3c4d5e6
Create Date: 2026-05-10 00:01:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2d3e4f5a6b7"
down_revision: str | Sequence[str] | None = "b1f2a3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create team_recruitment_post table."""
    op.create_table(
        "team_recruitment_post",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("team_id", sa.BigInteger, sa.ForeignKey("team.id"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("contact", sa.String(255), nullable=True),
        sa.Column("max_members", sa.Integer, nullable=True),
        sa.Column(
            "status",
            sa.String(50),
            nullable=False,
            server_default="OPEN",
        ),
        sa.Column("created_by", sa.Integer, nullable=False),
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
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_recruitment_team_id", "team_recruitment_post", ["team_id"])
    op.create_index("ix_recruitment_status", "team_recruitment_post", ["status"])


def downgrade() -> None:
    """Drop team_recruitment_post table."""
    op.drop_index("ix_recruitment_status", table_name="team_recruitment_post")
    op.drop_index("ix_recruitment_team_id", table_name="team_recruitment_post")
    op.drop_table("team_recruitment_post")
