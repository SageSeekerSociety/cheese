"""A model connection's service host, and its revocation while its machine is away

Revision ID: b7e4c2a9d5f1
Revises: c4e1a7d90b25
Create Date: 2026-10-12

``device_claude_login.service_host``: the host of the model service a
machine's owner pointed their own Claude Code at, so 「模型连接」 can say which
service it is (never the key). ``device_claude_login.revoked_at``: when its
owner removed the connection while the machine was offline; the machine logs
it out when it next connects, and nothing starts on it in between (#2991).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "b7e4c2a9d5f1"
down_revision: str | Sequence[str] | None = "c4e1a7d90b25"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("device_claude_login")
    op.add_column(
        "device_claude_login",
        sa.Column("service_host", sa.String(255), nullable=True),
    )
    op.add_column(
        "device_claude_login",
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("device_claude_login", "revoked_at")
    op.drop_column("device_claude_login", "service_host")
