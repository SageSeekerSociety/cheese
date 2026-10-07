"""A session has at most one sandbox home, with no "left" homes beside it

Nothing writes cloud_host_homes.left_at any more: a session that moves on
gives its home up. The one-home-per-session index stops reading the column,
so the column can be dropped once no release maps it.

Kept home archives whose conversation still has no notice are told again:
the previous migration untold them while the release before it still
served, and that release's sweep marked them told without placing the
notice in a task's or a 支线's conversation.

Revision ID: e241eeb9ffdb
Revises: a9c88363ec79
Create Date: 2026-10-07 23:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e241eeb9ffdb"
down_revision: str | Sequence[str] | None = "a9c88363ec79"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DELETE FROM cloud_host_homes WHERE left_at IS NOT NULL")
    op.execute(
        """
        UPDATE retained_home_archives r SET told_at = NULL
        WHERE NOT EXISTS (
            SELECT 1 FROM blocks b
            WHERE b.conversation_id = r.conversation_id
              AND b.meta->>'event_type' = 'sandbox_archives_kept'
        )
        """
    )
    op.drop_index("uq_cloud_host_homes_current_session", table_name="cloud_host_homes")
    op.create_index(
        "uq_cloud_host_homes_current_session",
        "cloud_host_homes",
        ["session_id"],
        unique=True,
        postgresql_where="session_id IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_index("uq_cloud_host_homes_current_session", table_name="cloud_host_homes")
    op.create_index(
        "uq_cloud_host_homes_current_session",
        "cloud_host_homes",
        ["session_id"],
        unique=True,
        postgresql_where="left_at IS NULL AND session_id IS NOT NULL",
    )
