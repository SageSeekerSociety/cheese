"""create_notification_table

Revision ID: 28c58249b703
Revises: 718ecf7d61d9
Create Date: 2026-05-02 22:15:58.135042

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "28c58249b703"
down_revision: str | Sequence[str] | None = "718ecf7d61d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 确保序列存在
    op.execute("CREATE SEQUENCE IF NOT EXISTS notification_seq")

    # 使用 DO 块检查表是否存在，若不存在则创建
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_tables WHERE tablename = 'notification') THEN
                CREATE TABLE notification (
                    id BIGINT DEFAULT nextval('notification_seq'::regclass) NOT NULL,
                    receiver_id BIGINT NOT NULL,
                    type VARCHAR(255) NOT NULL,
                    metadata JSONB,
                    content JSONB,
                    read BOOLEAN DEFAULT false NOT NULL,
                    is_aggregatable BOOLEAN DEFAULT false NOT NULL,
                    aggregation_key VARCHAR(255),
                    aggregate_until TIMESTAMPTZ,
                    finalized BOOLEAN DEFAULT true NOT NULL,
                    version BIGINT DEFAULT 0 NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ,
                    deleted_at TIMESTAMPTZ,
                    PRIMARY KEY (id)
                );
            END IF;
        END
        $$;
    """)

    # 索引同样使用 IF NOT EXISTS
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_notification_receiver_read_created ON notification (receiver_id, read, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_notification_aggregation ON notification (receiver_id, aggregation_key, aggregate_until)"
    )


def downgrade() -> None:
    # 降级时删除索引和表（仅用于回滚迁移，不影响正常环境）
    op.execute("DROP INDEX IF EXISTS idx_notification_aggregation")
    op.execute("DROP INDEX IF EXISTS idx_notification_receiver_read_created")
    op.execute("DROP TABLE IF EXISTS notification")
    op.execute("DROP SEQUENCE IF EXISTS notification_seq")
