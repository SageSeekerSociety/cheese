"""drop llm_subscriptions

Revision ID: d931a80be823
Revises: b4e1c2d9a7f3

ChatGPT subscription credentials are held by the metering proxy
(`deploy/metering-proxy/chatgpt-login.sh`), one credential per account. The
backend no longer imports, stores or refreshes them, so the table that held the
imported tokens goes, with both of its indexes. An account that was imported
here is moved with `chatgpt-login.sh import` or logged in again.
"""

from collections.abc import Sequence

import sqlalchemy as sa

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
    # Gives back the table's shape only: the tokens it held are gone, and an
    # empty table is what a downgrade can honestly restore. It exists because
    # migration tests downgrade through head to reach older revisions.
    op.create_table(
        "llm_subscriptions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("account_email", sa.String(320), nullable=True),
        sa.Column("chatgpt_account_id", sa.String(128), nullable=True),
        sa.Column("id_token_subject", sa.String(128), nullable=True),
        sa.Column("access_token_enc", sa.Text(), nullable=True),
        sa.Column("refresh_token_enc", sa.Text(), nullable=True),
        sa.Column("id_token_enc", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_refresh_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_refresh_error", sa.Text(), nullable=True),
        sa.Column("quota_snapshot", sa.JSON(), nullable=True),
        sa.Column("quota_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("linked_model_name", sa.String(64), nullable=True),
        sa.Column("upstream_model", sa.String(200), nullable=True),
        sa.Column("flow_device_auth_id", sa.String(128), nullable=True),
        sa.Column("flow_user_code", sa.String(32), nullable=True),
        sa.Column("flow_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("flow_last_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("flow_target_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_handle", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_llm_subscriptions_created_at",
        "llm_subscriptions",
        [sa.text("created_at DESC")],
    )
    op.create_index(
        "uq_llm_subscriptions_live_provider",
        "llm_subscriptions",
        ["provider"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('pending','active','refresh_failed','reauth_required')"
        ),
    )
