"""Store the runtime cloud machine limit.

Revision ID: d8a6b5c4e731
Revises: c2d7e9f1a718
"""

import sqlalchemy as sa

from alembic import op

revision = "d8a6b5c4e731"
down_revision = "c2d7e9f1a718"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "machine_limit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_machine_limit_singleton"),
        sa.CheckConstraint("value > 0", name="ck_machine_limit_positive"),
    )
    op.create_table(
        "team_machine_limit",
        sa.Column(
            "team_id",
            sa.BigInteger(),
            sa.ForeignKey("team.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.CheckConstraint("value > 0", name="ck_team_machine_limit_positive"),
    )
    op.add_column(
        "compute_grants",
        sa.Column(
            "team_id",
            sa.BigInteger(),
            sa.ForeignKey("team.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index("ix_compute_grants_team_id", "compute_grants", ["team_id"])
    op.alter_column("compute_grants", "project_id", nullable=True)
    # Retain every project's restriction and balance; ownership alone moves.
    op.execute(
        "UPDATE compute_grants g SET team_id = p.team_id FROM projects p WHERE g.project_id = p.id"
    )


def downgrade() -> None:
    # Team-wide grants cannot be mapped back to one project without losing funds.
    connection = op.get_bind()
    if connection.execute(
        sa.text("SELECT 1 FROM compute_grants WHERE project_id IS NULL LIMIT 1")
    ).first():
        raise RuntimeError("Cannot downgrade while team-wide credit grants exist")
    op.alter_column("compute_grants", "project_id", nullable=False)
    op.drop_index("ix_compute_grants_team_id", table_name="compute_grants")
    op.drop_column("compute_grants", "team_id")
    op.drop_table("team_machine_limit")
    op.drop_table("machine_limit")
