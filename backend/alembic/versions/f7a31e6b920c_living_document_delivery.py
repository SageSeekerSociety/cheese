"""Immutable raw history, completed receipts and durable refresh records.

Revision ID: f7a31e6b920c
Revises: e9c2a71d4b60
"""

import sqlalchemy as sa

from alembic import op

revision = "f7a31e6b920c"
down_revision = "e9c2a71d4b60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Historical event identity is evidence, not a mutable live relationship.
    op.drop_constraint(
        "living_doc_versions_event_id_fkey", "living_doc_versions", type_="foreignkey"
    )
    op.execute("""
        CREATE FUNCTION living_doc_history_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' AND NOT EXISTS (
                SELECT 1 FROM blocks WHERE id = OLD.document_id
            ) THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'living document history is immutable'
                USING ERRCODE = '23514';
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER living_doc_history_immutable
        BEFORE UPDATE OR DELETE ON living_doc_versions
        FOR EACH ROW EXECUTE FUNCTION living_doc_history_immutable()
    """)
    op.execute("""
        CREATE FUNCTION living_doc_receipt_complete() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM living_doc_operations
                WHERE id = NEW.id AND (receipt IS NULL OR receipt::text = 'null')
            ) THEN
                RAISE EXCEPTION 'document operation must commit its receipt'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END;
        $$
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER living_doc_receipt_complete
        AFTER INSERT OR UPDATE ON living_doc_operations
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION living_doc_receipt_complete()
    """)
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


def downgrade() -> None:
    op.drop_table("living_doc_refreshes")
    op.execute("DROP TRIGGER living_doc_receipt_complete ON living_doc_operations")
    op.execute("DROP FUNCTION living_doc_receipt_complete()")
    op.execute("DROP TRIGGER living_doc_history_immutable ON living_doc_versions")
    op.execute("DROP FUNCTION living_doc_history_immutable()")
    op.create_foreign_key(
        "living_doc_versions_event_id_fkey",
        "living_doc_versions",
        "blocks",
        ["event_id"],
        ["id"],
        ondelete="SET NULL",
    )
