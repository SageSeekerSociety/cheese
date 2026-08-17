"""team.personal_owner_user_id: personal single-member teams (v4: 个人 = 单人真团队)

A user's personal team owns their personal compute and backs their personal projects.
NULL = a normal shared team. One per user, auto-provisioned. Additive only.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1e5b3a9c724"
down_revision: str | Sequence[str] | None = "c9a4e2f7b118"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "team",
        sa.Column("personal_owner_user_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        op.f("ix_team_personal_owner_user_id"),
        "team",
        ["personal_owner_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_team_personal_owner_user_id"), table_name="team")
    op.drop_column("team", "personal_owner_user_id")
