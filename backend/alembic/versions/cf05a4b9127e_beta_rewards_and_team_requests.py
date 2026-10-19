"""Beta rewards and team plan / top-up requests.

Revision ID: cf05a4b9127e
Revises: 7dba632a1e9f
"""

import sqlalchemy as sa

from alembic import op

revision = "cf05a4b9127e"
down_revision = "7dba632a1e9f"
branch_labels = None
depends_on = None


def _timestamps():
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    ]


def upgrade():
    op.create_table(
        "credit_rewards",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("credits", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(128), nullable=False),
        sa.Column("actor_handle", sa.String(64), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["team.id"],
            name="fk_credit_rewards_team_id_team",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_credit_rewards_team_id", "credit_rewards", ["team_id"])
    op.create_table(
        "credit_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("credits", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("requester_handle", sa.String(64), nullable=False),
        sa.Column("handled_by", sa.String(64), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["team.id"],
            name="fk_credit_requests_team_id_team",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_credit_requests_team_id", "credit_requests", ["team_id"])
    op.create_index(
        "uq_credit_requests_pending",
        "credit_requests",
        ["team_id", "kind"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade():
    op.drop_table("credit_requests")
    op.drop_table("credit_rewards")
