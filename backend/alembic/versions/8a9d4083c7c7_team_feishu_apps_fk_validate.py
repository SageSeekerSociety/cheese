"""team_feishu_apps.team_id → team：验证外键

Revision ID: 8a9d4083c7c7
Revises: 6bfa39e854c4
Create Date: 2026-11-25

先用 ``with_lock_retries`` 拿下两张表（拿不到就放手、稍后再试，不排在活着的请求后面
等到超时），再在同一个事务里验证；``team_feishu_apps`` 是新表、很小，占着 ``team`` 的
那一下很短。重跑时已经验证过的约束再验证一次是空操作。
"""

from collections.abc import Sequence

from migration_helpers import with_lock_retries

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8a9d4083c7c7"
down_revision: str | Sequence[str] | None = "6bfa39e854c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The two tables are taken with the repo's lock retries first, so the
    # validation never waits behind a live request and times out: on a busy
    # moment it lets go and tries again. ``team_feishu_apps`` is new and tiny,
    # so holding ``team`` for it is a moment.
    with_lock_retries("team_feishu_apps, team")
    op.execute(
        "ALTER TABLE team_feishu_apps "
        "VALIDATE CONSTRAINT fk_team_feishu_apps_team_id_team"
    )


def downgrade() -> None:
    """Leaves the constraint validated: there is no going back to an
    unvalidated state, and the migration before this one drops it."""
