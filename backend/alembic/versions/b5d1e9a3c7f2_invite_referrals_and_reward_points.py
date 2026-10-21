"""Personal invite codes, who invited whom, and contribution points

Revision ID: b5d1e9a3c7f2
Revises: c3e8a1d5f240
Create Date: 2026-10-21

Each person gets one invite code of their own (``invite_code.owner_user_id``;
an administrator's codes keep it null), which gains its extra uses once
(``unlocked_at``). ``invite_referrals`` records, per invitee, whose code they
registered with and how far they have got. ``credit_rewards.points`` is the
contribution score a reward carries next to its credits, and reward packs get
their own ``compute_grants.source``.

Existing rows: administrator codes stay as they are; earlier rewards get 0
points except accepted feedback, whose points equal its credits under the
team's feedback rules; the beta and feedback packs already issued move from
``admin_grant`` to ``reward``.

The previous release keeps serving while this runs, so constraints on existing
tables are added ``NOT VALID`` and validated afterwards, and the owner's unique
index is built ``CONCURRENTLY`` before it becomes the constraint.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "b5d1e9a3c7f2"
down_revision: str | Sequence[str] | None = "c3e8a1d5f240"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOURCES = "'plan_period', 'task_earmark', 'purchase', 'admin_grant'"


def upgrade() -> None:
    with_lock_retries('invite_code, "user", credit_rewards, compute_grants')
    op.add_column(
        "invite_code", sa.Column("owner_user_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "invite_code",
        sa.Column("unlocked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_invite_code_owner_user_id_user",
        "invite_code",
        "user",
        ["owner_user_id"],
        ["id"],
        ondelete="CASCADE",
        postgresql_not_valid=True,
    )
    op.create_table(
        "invite_referrals",
        sa.Column("invitee_user_id", sa.Integer(), nullable=False),
        sa.Column("inviter_user_id", sa.Integer(), nullable=False),
        sa.Column("code_id", sa.Integer(), nullable=False),
        sa.Column(
            "registered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("onboarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["invitee_user_id"],
            ["user.id"],
            name="fk_invite_referrals_invitee_user_id_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["inviter_user_id"],
            ["user.id"],
            name="fk_invite_referrals_inviter_user_id_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["code_id"],
            ["invite_code.id"],
            name="fk_invite_referrals_code_id_invite_code",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("invitee_user_id"),
    )
    op.create_index(
        "ix_invite_referrals_inviter_user_id", "invite_referrals", ["inviter_user_id"]
    )
    op.create_index("ix_invite_referrals_code_id", "invite_referrals", ["code_id"])
    op.add_column(
        "credit_rewards",
        sa.Column("points", sa.Integer(), server_default="0", nullable=False),
    )
    op.execute(
        "UPDATE credit_rewards SET points = credits "
        "WHERE key LIKE 'feedback:%' AND points = 0"
    )
    op.drop_constraint("ck_compute_grants_source", "compute_grants", type_="check")
    op.create_check_constraint(
        "ck_compute_grants_source",
        "compute_grants",
        f"source IN ({_SOURCES}, 'reward')",
        postgresql_not_valid=True,
    )
    # The beta and feedback packs issued so far were rewards filed as
    # administrator grants; their reasons are the ones CreditIncentives wrote.
    op.execute(
        "UPDATE compute_grants SET source = 'reward' WHERE source = 'admin_grant' "
        "AND (reason = 'Beta participation' OR reason LIKE 'Accepted feedback FB-%')"
    )
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TABLE invite_code VALIDATE CONSTRAINT "
            "fk_invite_code_owner_user_id_user"
        )
        op.execute(
            "ALTER TABLE compute_grants VALIDATE CONSTRAINT ck_compute_grants_source"
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS uq_invite_code_owner_user_id")
        op.create_index(
            "uq_invite_code_owner_user_id",
            "invite_code",
            ["owner_user_id"],
            unique=True,
            postgresql_concurrently=True,
        )
    op.execute(
        "ALTER TABLE invite_code ADD CONSTRAINT uq_invite_code_owner_user_id "
        "UNIQUE USING INDEX uq_invite_code_owner_user_id"
    )


def downgrade() -> None:
    with_lock_retries('invite_code, "user", credit_rewards, compute_grants')
    op.execute(
        "UPDATE compute_grants SET source = 'admin_grant' WHERE source = 'reward'"
    )
    op.drop_constraint("ck_compute_grants_source", "compute_grants", type_="check")
    op.create_check_constraint(
        "ck_compute_grants_source", "compute_grants", f"source IN ({_SOURCES})"
    )
    op.drop_column("credit_rewards", "points")
    op.drop_table("invite_referrals")
    op.drop_constraint("uq_invite_code_owner_user_id", "invite_code", type_="unique")
    op.drop_constraint(
        "fk_invite_code_owner_user_id_user", "invite_code", type_="foreignkey"
    )
    op.drop_column("invite_code", "unlocked_at")
    op.drop_column("invite_code", "owner_user_id")
