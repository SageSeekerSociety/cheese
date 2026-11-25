"""team_feishu_apps.team_id → team：验证外键

Revision ID: 8a9d4083c7c7
Revises: 6bfa39e854c4
Create Date: 2026-11-25

``VALIDATE CONSTRAINT`` 只拿 SHARE UPDATE EXCLUSIVE，不挡 ``team`` 的读写。
重跑时已经验证过的约束再验证一次是空操作。
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8a9d4083c7c7"
down_revision: str | Sequence[str] | None = "6bfa39e854c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TABLE team_feishu_apps "
            "VALIDATE CONSTRAINT fk_team_feishu_apps_team_id_team"
        )


def downgrade() -> None:
    """Leaves the constraint validated: there is no going back to an
    unvalidated state, and the migration before this one drops it."""
