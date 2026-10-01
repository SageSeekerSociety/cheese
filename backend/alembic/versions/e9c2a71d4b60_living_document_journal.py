"""Living-document raw versions, write receipts and first-creation locks.

Revision ID: e9c2a71d4b60
Revises: e5c1a9f7b204
"""

import sqlalchemy as sa

from alembic import op

revision = "e9c2a71d4b60"
down_revision = "e5c1a9f7b204"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "living_doc_locks",
        sa.Column(
            "room_id",
            sa.Uuid(),
            sa.ForeignKey("topics.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_table(
        "living_doc_versions",
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
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("previous_version", sa.Integer(), nullable=True),
        sa.Column("base_version", sa.Integer(), nullable=True),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=True),
        sa.Column(
            "event_id",
            sa.Uuid(),
            sa.ForeignKey("blocks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_id", "version", name="uq_living_doc_version"),
    )
    op.create_index(
        "ix_living_doc_versions_room_id", "living_doc_versions", ["room_id"]
    )
    op.create_table(
        "living_doc_operations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "room_id",
            sa.Uuid(),
            sa.ForeignKey("topics.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("receipt", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "room_id", "actor", "action", "operation_id", name="uq_living_doc_operation"
        ),
    )
    op.create_index(
        "ix_living_doc_operations_room_id", "living_doc_operations", ["room_id"]
    )


def downgrade() -> None:
    op.drop_table("living_doc_operations")
    op.drop_table("living_doc_versions")
    op.drop_table("living_doc_locks")
