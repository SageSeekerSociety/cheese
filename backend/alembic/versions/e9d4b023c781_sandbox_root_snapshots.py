"""Keep session root diffs independently of disposable sandbox homes."""

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision = "e9d4b023c781"
down_revision = "d8c3a912b670"
branch_labels = None
depends_on = None


def upgrade():
    with_lock_retries("agent_sessions")
    op.create_table(
        "sandbox_root_snapshots",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("image", sa.String(255), nullable=False),
        sa.Column("key", sa.Text(), nullable=True),
        sa.Column("size", sa.BigInteger(), nullable=True),
        sa.Column("md5", sa.String(32), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["agent_sessions.id"],
            ondelete="CASCADE",
            name="fk_sandbox_root_snapshots_session_id_agent_sessions",
        ),
        sa.PrimaryKeyConstraint("session_id"),
    )


def downgrade():
    op.drop_table("sandbox_root_snapshots")
