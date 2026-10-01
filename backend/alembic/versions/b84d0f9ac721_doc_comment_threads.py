"""Persistent document comment threads and historical anchor evidence.

Revision ID: b84d0f9ac721
Revises: a27d91f0b63e
"""

import sqlalchemy as sa

from alembic import op

revision = "b84d0f9ac721"
down_revision = "a27d91f0b63e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "doc_comment_threads",
        sa.Column(
            "comment_id",
            sa.Uuid(),
            sa.ForeignKey("blocks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("state", sa.String(16), server_default="open", nullable=False),
        sa.Column("reply_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("anchor", sa.JSON(), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_doc_comment_revision"),
        sa.CheckConstraint("reply_count >= 0", name="ck_doc_comment_reply_count"),
        sa.CheckConstraint(
            "state IN ('open', 'resolved')", name="ck_doc_comment_state"
        ),
    )
    op.create_table(
        "doc_comment_replies",
        sa.Column(
            "block_id",
            sa.Uuid(),
            sa.ForeignKey("blocks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "comment_id",
            sa.Uuid(),
            sa.ForeignKey("doc_comment_threads.comment_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.UniqueConstraint("comment_id", "sequence", name="uq_doc_comment_sequence"),
        sa.CheckConstraint("sequence >= 1", name="ck_doc_comment_sequence"),
    )
    op.create_index(
        "ix_doc_comment_replies_comment_id", "doc_comment_replies", ["comment_id"]
    )
    # Do not assign today's version to old comments or infer a span from a quote.
    # A node already deleted before this migration cannot be reconstructed.
    op.execute("""
        INSERT INTO doc_comment_threads (comment_id, anchor)
        SELECT c.id, json_build_object(
            'node_id', c.reply_to::text, 'quote', c.anchor_quote,
            'node_content', CASE WHEN n.kind = 'doc_node' THEN n.content END,
            'document_id', CASE WHEN n.kind = 'doc_node' THEN n.struct_parent::text END,
            'base_version', NULL, 'start', NULL, 'end', NULL,
            'offset_unit', 'utf8-bytes'
        )
        FROM blocks c LEFT JOIN blocks n ON n.id = c.reply_to
        WHERE c.kind = 'comment' AND c.task_id IS NULL
          AND (n.id IS NULL OR n.kind <> 'comment')
    """)
    op.execute("""
        CREATE FUNCTION doc_comment_anchor_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.comment_id IS DISTINCT FROM OLD.comment_id
               OR NEW.anchor::jsonb IS DISTINCT FROM OLD.anchor::jsonb THEN
                RAISE EXCEPTION 'document comment anchor is historical evidence'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER doc_comment_anchor_immutable
        BEFORE UPDATE ON doc_comment_threads
        FOR EACH ROW EXECUTE FUNCTION doc_comment_anchor_immutable()
    """)


def downgrade() -> None:
    # Reply content stays readable as comment blocks; remove only thread metadata.
    op.drop_table("doc_comment_replies")
    op.drop_table("doc_comment_threads")
    op.execute("DROP FUNCTION doc_comment_anchor_immutable()")
