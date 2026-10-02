"""Every credit pack belongs to a team

Revision ID: f2a9c4e7b318
Revises: b3f8d2e6a174
Create Date: 2026-10-02

A pack now says where it came from (``source``) and when it lapses
(``expires_at``). The per-person
monthly grants become plan packs on each person's personal team, which is
created here for anyone who had a grant but no personal team yet, exactly as
``TeamRepository.create_personal_team`` would. Grants on one project become task
earmarks; the remaining team grants were issued by an administrator.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f2a9c4e7b318"
down_revision: str | Sequence[str] | None = "b3f8d2e6a174"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOURCES = "'plan_period', 'task_earmark', 'purchase', 'admin_grant'"


def upgrade() -> None:
    op.add_column("compute_grants", sa.Column("source", sa.String(32), nullable=True))
    op.add_column("compute_grants", sa.Column("period_start", sa.Date(), nullable=True))
    op.add_column(
        "compute_grants",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )

    # A personal team for everyone whose credits are about to move onto one.
    op.execute(
        """
        INSERT INTO team (id, name, intro, description, avatar_id,
                          personal_owner_user_id, created_at, updated_at)
        SELECT nextval('team_seq'), '个人', '', '', 0, owners.user_id, now(), now()
        FROM (SELECT DISTINCT user_id FROM compute_grants
              WHERE user_id IS NOT NULL) AS owners
        WHERE NOT EXISTS (
            SELECT 1 FROM team t
            WHERE t.personal_owner_user_id = owners.user_id
              AND t.deleted_at IS NULL
        )
        """
    )
    op.execute(
        """
        INSERT INTO team_user_relation (id, team_id, user_id, role,
                                        created_at, updated_at)
        SELECT nextval('team_user_relation_seq'), t.id, t.personal_owner_user_id,
               0, now(), now()
        FROM team t
        WHERE t.personal_owner_user_id IS NOT NULL AND t.deleted_at IS NULL
          AND NOT EXISTS (
            SELECT 1 FROM team_user_relation r
            WHERE r.team_id = t.id AND r.user_id = t.personal_owner_user_id
              AND r.deleted_at IS NULL
          )
        """
    )

    # A person's monthly grant: the plan pack of their personal team, lapsing
    # when its month ends in the platform's timezone.
    op.execute(
        """
        UPDATE compute_grants g
        SET team_id = t.id,
            source = 'plan_period',
            period_start = g.month,
            expires_at = ((g.month + interval '1 month')::timestamp
                          AT TIME ZONE 'Asia/Shanghai')
        FROM team t
        WHERE g.user_id IS NOT NULL
          AND t.personal_owner_user_id = g.user_id AND t.deleted_at IS NULL
        """
    )
    op.execute(
        """
        UPDATE compute_grants g
        SET team_id = p.team_id
        FROM projects p
        WHERE g.project_id = p.id AND g.team_id IS NULL
        """
    )
    op.execute(
        "UPDATE compute_grants SET source = 'task_earmark' "
        "WHERE source IS NULL AND project_id IS NOT NULL"
    )
    op.execute("UPDATE compute_grants SET source = 'admin_grant' WHERE source IS NULL")
    # Nothing can pay for a grant that names no team; none should exist.
    op.execute("DELETE FROM compute_grants WHERE team_id IS NULL")

    op.drop_index("uq_compute_grants_user_month", table_name="compute_grants")
    op.drop_column("compute_grants", "month")
    op.drop_column("compute_grants", "user_id")
    op.alter_column("compute_grants", "team_id", nullable=False)
    op.alter_column("compute_grants", "source", nullable=False)
    op.create_check_constraint(
        "ck_compute_grants_source", "compute_grants", f"source IN ({_SOURCES})"
    )
    op.create_index(
        "uq_compute_grants_plan_period",
        "compute_grants",
        ["team_id", "period_start"],
        unique=True,
        postgresql_where=sa.text("source = 'plan_period'"),
    )


def downgrade() -> None:
    op.drop_index("uq_compute_grants_plan_period", table_name="compute_grants")
    op.drop_constraint("ck_compute_grants_source", "compute_grants", type_="check")
    op.add_column(
        "compute_grants",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column("compute_grants", sa.Column("month", sa.Date(), nullable=True))
    op.alter_column("compute_grants", "team_id", nullable=True)
    op.execute(
        """
        UPDATE compute_grants g
        SET user_id = t.personal_owner_user_id, month = g.period_start,
            team_id = NULL
        FROM team t
        WHERE g.team_id = t.id AND g.source = 'plan_period'
          AND t.personal_owner_user_id IS NOT NULL
        """
    )
    op.create_index(
        "uq_compute_grants_user_month",
        "compute_grants",
        ["user_id", "month"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.drop_column("compute_grants", "expires_at")
    op.drop_column("compute_grants", "period_start")
    op.drop_column("compute_grants", "source")
