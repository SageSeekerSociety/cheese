"""Comment threads drop the captured anchor: the words are marked in the document."""

import sqlalchemy as sa

from alembic import op

revision = "1ca025b94a3e"
down_revision = "e5a91c3d7b40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The trigger kept the captured anchor from changing; with no anchor
    # there is nothing for it to guard.
    op.execute("DROP TRIGGER doc_comment_anchor_immutable ON doc_comment_threads")
    op.execute("DROP FUNCTION doc_comment_anchor_immutable()")
    op.drop_column("doc_comment_threads", "anchor")


def downgrade() -> None:
    op.add_column(
        "doc_comment_threads",
        sa.Column("anchor", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.alter_column("doc_comment_threads", "anchor", server_default=None)
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
