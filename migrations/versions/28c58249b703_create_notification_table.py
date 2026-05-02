"""create_notification_table

Revision ID: 28c58249b703
Revises: 718ecf7d61d9
Create Date: 2026-05-02 22:15:58.135042

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "9b3dbc5ade4c"
down_revision: str | Sequence[str] | None = "718ecf7d61d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. 创建序列（如果不存在）
    op.execute("CREATE SEQUENCE IF NOT EXISTS notification_seq")

    # 2. 创建 notification 表
    op.create_table(
        "notification",
        sa.Column(
            "id",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("nextval('notification_seq'::regclass)"),
        ),
        sa.Column("receiver_id", sa.BigInteger(), nullable=False),
        sa.Column("type", sa.String(length=255), nullable=False),
        sa.Column("metadata", JSONB, nullable=True),
        sa.Column("content", JSONB, nullable=True),
        sa.Column("read", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_aggregatable", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("aggregation_key", sa.String(length=255), nullable=True),
        sa.Column("aggregate_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # 3. 创建索引（与模型中的 index 一致）
    op.create_index(
        "idx_notification_receiver_read_created",
        "notification",
        ["receiver_id", "read", "created_at"],
        postgresql_using="btree",
    )
    op.create_index(
        "idx_notification_aggregation",
        "notification",
        ["receiver_id", "aggregation_key", "aggregate_until"],
        postgresql_using="btree",
    )


def downgrade() -> None:
    op.drop_index("idx_notification_aggregation", table_name="notification")
    op.drop_index("idx_notification_receiver_read_created", table_name="notification")
    op.drop_table("notification")
    op.execute("DROP SEQUENCE IF EXISTS notification_seq")
