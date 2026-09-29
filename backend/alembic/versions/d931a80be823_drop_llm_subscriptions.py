"""drop llm_subscriptions

Revision ID: d931a80be823
Revises: 2d2fc3a8ce36

ChatGPT subscription credentials are held by the metering proxy
(`deploy/metering-proxy/chatgpt-login.sh`), one credential per account. The
backend no longer imports, stores or refreshes them, so the table that held the
imported tokens goes, with both of its indexes. An account that was imported
here is logged in again with the script.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d931a80be823"
down_revision: str | Sequence[str] | None = "b4e1c2d9a7f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("uq_llm_subscriptions_live_provider", table_name="llm_subscriptions")
    op.drop_index("ix_llm_subscriptions_created_at", table_name="llm_subscriptions")
    op.drop_table("llm_subscriptions")


def downgrade() -> None:
    raise RuntimeError(
        "Irreversible: the tokens in llm_subscriptions were dropped with the "
        "table, and an empty table would only give back its shape. To go back "
        "before this revision, restore from a backup taken before it."
    )
