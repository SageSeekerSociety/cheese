"""Retire 启星研导: task advice, its conversations, and the daily AI quota

Revision ID: e5c1a9f7b204
Revises: d2b8f4a61c90
Create Date: 2026-10-01 06:30:00

The task page's generated advice and its chat were replaced by the person's
芝士 (assistant_conversations, #2285), and the per-person daily quota by
personal credits. Nothing reads these tables any more. Their rows are dropped
without a backup: the owner decided the old conversations need not be kept.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e5c1a9f7b204"
down_revision: str | Sequence[str] | None = "d2b8f4a61c90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "ai_message",
    "ai_conversation",
    "task_ai_advice_context",
    "task_ai_advice",
    "user_ai_quota",
)


#: Their standalone id sequences, which dropping a table does not take with it.
_SEQUENCES = (
    "ai_message_seq",
    "ai_conversation_seq",
    "task_ai_advice_context_seq",
    "user_ai_quota_seq",
)


def upgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    for sequence in _SEQUENCES:
        op.execute(f"DROP SEQUENCE IF EXISTS {sequence}")


def downgrade() -> None:
    raise RuntimeError(
        "启星研导's tables were dropped with their rows; there is nothing to restore."
    )
