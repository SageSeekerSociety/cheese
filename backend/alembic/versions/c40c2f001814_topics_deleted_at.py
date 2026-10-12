"""topics.deleted_at: a channel a person deleted, waiting for its cleanup

Deleting a channel is archiving it and not coming back: the row keeps its
content until `topic/retire.py` has finished taking the channel's homes,
worktrees and transcripts off its devices, and is then removed. This column is
that state — the channel is gone from every list, and 取消归档 is not offered
for it.

Additive: the previous image neither reads nor writes the column, and NULL —
every row it writes — is exactly 「不是删除」.

Revision ID: c40c2f001814
Revises: e81c4b7a2f05
"""

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision = "c40c2f001814"
down_revision = "e81c4b7a2f05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with_lock_retries("topics")
    op.add_column("topics", sa.Column("deleted_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("topics", "deleted_at")
