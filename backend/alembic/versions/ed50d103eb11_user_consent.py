"""Record each person's consent to the legal documents (#1486)."""

import sqlalchemy as sa

from alembic import op

revision = "ed50d103eb11"
down_revision = "514d7c9cb013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_consent",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("document", sa.String(32), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("entry", sa.String(32), nullable=False),
        sa.Column("ip", sa.String(512), nullable=False),
        sa.Column("user_agent", sa.String(1024), nullable=False),
    )
    op.create_index("ix_user_consent_user_id", "user_consent", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_consent_user_id", table_name="user_consent")
    op.drop_table("user_consent")
