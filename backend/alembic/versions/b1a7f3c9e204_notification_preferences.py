"""per-user notification preferences (设计稿 通知设置页)

Revision ID: b1a7f3c9e204
Revises: 4562fd5e0eeb
Create Date: 2026-10-04 09:30:00.000000

一张表，一人一行：三个渠道的总开关、免打扰时段、摘要频率，加上矩阵那八行的渠道
开关（JSONB）。加表，不动存量：没有行的人一律按设计稿的默认（`preferences.py`），
所以这次迁移不需要回填，也没什么可回滚坏的。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1a7f3c9e204"
down_revision: str | Sequence[str] | None = "4562fd5e0eeb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification_preference",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "in_app_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "push_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "email_mode",
            sa.String(length=16),
            nullable=False,
            server_default="digest",
        ),
        sa.Column(
            "quiet_hours_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "quiet_hours_start",
            sa.String(length=5),
            nullable=False,
            server_default="22:00",
        ),
        sa.Column(
            "quiet_hours_end",
            sa.String(length=5),
            nullable=False,
            server_default="08:00",
        ),
        sa.Column(
            "digest_cadence",
            sa.String(length=16),
            nullable=False,
            server_default="weekly",
        ),
        sa.Column(
            "events",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name="notification_preference_user_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name="notification_preference_pkey"),
    )


def downgrade() -> None:
    op.drop_table("notification_preference")
