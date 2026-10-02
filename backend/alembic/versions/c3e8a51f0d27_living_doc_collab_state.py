"""The living document's collaborative state replaces refresh delivery.

The document is edited live through the collaboration service, which keeps a
Yjs state per room and stores it here alongside the exported Markdown. Editors
receive changes over that connection, so the at-least-once refresh outbox for
reconnecting editors goes.

Revision ID: c3e8a51f0d27
Revises: a6d3f1c9e842
"""

import sqlalchemy as sa

from alembic import op

revision = "c3e8a51f0d27"
down_revision = "a6d3f1c9e842"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "living_doc_states",
        sa.Column(
            "room_id",
            sa.Uuid(),
            sa.ForeignKey("topics.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("state", sa.LargeBinary(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.drop_table("living_doc_refreshes")


def downgrade() -> None:
    op.create_table(
        "living_doc_refreshes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "room_id",
            sa.Uuid(),
            sa.ForeignKey("topics.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("blocks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("room_id", "version", name="uq_living_doc_refresh"),
    )
    op.create_index(
        "ix_living_doc_refreshes_pending", "living_doc_refreshes", ["dispatched_at"]
    )
    op.drop_table("living_doc_states")
