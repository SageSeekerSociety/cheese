"""team_feishu_apps.team_id → team：外键先 NOT VALID 地加上

Revision ID: 6bfa39e854c4
Revises: 4288e24987e2
Create Date: 2026-11-25

``NOT VALID`` 只在目录里登记，不扫表；锁 ``team`` 的那一下由 ``with_lock_retries`` 排队
拿，不会排在活着的事务后面占着锁。验证在下一个迁移里做。重跑时 ``team_feishu_apps`` 上已经有这个约束就跳过
（按名字和所在的表一起认，别的表上同名的约束不算）。
"""

from collections.abc import Sequence

from migration_helpers import with_lock_retries

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6bfa39e854c4"
down_revision: str | Sequence[str] | None = "4288e24987e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAME = "fk_team_feishu_apps_team_id_team"


def upgrade() -> None:
    with_lock_retries("team_feishu_apps, team")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = '{NAME}'
                    AND conrelid = 'team_feishu_apps'::regclass
            ) THEN
                ALTER TABLE team_feishu_apps ADD CONSTRAINT {NAME}
                    FOREIGN KEY (team_id) REFERENCES team (id) ON DELETE CASCADE
                    NOT VALID;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    with_lock_retries("team_feishu_apps, team")
    op.execute(f"ALTER TABLE team_feishu_apps DROP CONSTRAINT IF EXISTS {NAME}")
