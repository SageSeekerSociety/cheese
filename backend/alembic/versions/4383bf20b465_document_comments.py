"""Comments on a document get their own table, keyed by the document

A comment on a room's living document was a `blocks` row (`kind = comment`) in
the room, with `doc_comment_threads` holding its thread's state and
`doc_comment_replies` numbering its replies. A block needs a room, and a
document of the project's own has none; so comments move to
`document_comments`, under the document, and a thread's state to
`document_threads`.

What every reader sees is kept:

- Every comment keeps its id. The editor marks the words a thread is about with
  the opening comment's id (`commentAnchor` in the Yjs state), and that mark
  goes on pointing at it.
- An opening comment is a comment block that is not a numbered reply and does
  not answer another comment. Its document is the living document of the room
  it was written in, made empty here for a room that has comments and none.
  That includes a comment written from a thread's card (`task_id` set), which
  the room's document marked but the thread list never showed.
- A numbered reply keeps its number. An early reply that only answered another
  comment (`reply_to`, before replies were numbered) was shown nowhere; it is
  numbered after its thread's numbered replies, oldest first, so its thread
  shows it.
- A thread's state and revision carry over; a thread without a state row starts
  open at revision 1, which is what reading it made of it.

The comment blocks are then deleted, with `doc_comment_threads` and
`doc_comment_replies`, and `blocks.anchor_quote`, which only comments set.
`comment` stays a label of the `blockkind` enum, unused: Postgres cannot drop an
enum label without rebuilding the type.

The backend this deploy replaces is still serving while this runs. Every table
the migration changes, and every table a foreign key it adds or a row it
deletes reaches, is locked up front, all at once or not at all (`_lock_all`),
so a live request waits for it instead of deadlocking against it.

Revision ID: 4383bf20b465
Revises: be894366c0a5
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "4383bf20b465"
down_revision: str | Sequence[str] | None = "be894366c0a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _text_fields(texts: Sequence[str], keywords: Sequence[str]) -> str:
    # As in 2d2fc3a8ce36. A space after every ':' — `op.execute` wraps the
    # string in `text()`, which reads `:word` as a bind parameter.
    fields = []
    for column in texts:
        fields.append(
            f'"{column}": {{"tokenizer": {{"type": "jieba"}}, "record": "position"}}'
        )
        fields.append(
            f'"{column}_ngram": {{"column": "{column}", "tokenizer": '
            '{"type": "ngram", "min_gram": 2, "max_gram": 3, "prefix_only": false}}'
        )
    for column in keywords:
        fields.append(f'"{column}": {{"tokenizer": {{"type": "keyword"}}}}')
    return "{" + ", ".join(fields) + "}"


def _lock_all(tables: str) -> None:
    """Hold every table this migration touches before it writes anything, or
    none of them: each attempt takes them all at once without waiting
    (`NOWAIT`), and one that cannot is undone whole and tried again shortly.
    Waiting with some held, the migration could hold what a live request needs
    while it waits on that request: a deadlock, which Postgres settles by
    killing one of them. Holding nothing while it waits, it never blocks
    anyone until it has everything."""
    op.execute(f"""
        DO $$
        DECLARE attempts integer := 0;
        BEGIN
            LOOP
                BEGIN
                    LOCK TABLE {tables} IN ACCESS EXCLUSIVE MODE NOWAIT;
                    EXIT;
                EXCEPTION WHEN lock_not_available THEN
                    attempts := attempts + 1;
                    IF attempts >= 1200 THEN
                        RAISE;
                    END IF;
                    PERFORM pg_sleep(0.05);
                END;
            END LOOP;
        END
        $$
    """)


def upgrade() -> None:
    _lock_all(
        "topics, projects, tasks, documents, blocks, block_reactions,"
        " doc_comment_threads, doc_comment_replies"
    )

    op.create_table(
        "document_comments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "thread_id",
            sa.Uuid(),
            sa.ForeignKey("document_comments.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("sequence", sa.Integer(), nullable=True),
        sa.Column("author", sa.String(128), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("anchor_quote", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "thread_id", "sequence", name="uq_document_comment_sequence"
        ),
        sa.CheckConstraint(
            "(thread_id IS NULL) = (sequence IS NULL)",
            name="ck_document_comment_reply_sequence",
        ),
    )
    op.create_index(
        "ix_document_comments_document_id", "document_comments", ["document_id"]
    )
    op.create_index(
        "ix_document_comments_thread_id", "document_comments", ["thread_id"]
    )
    op.create_table(
        "document_threads",
        sa.Column(
            "id",
            sa.Uuid(),
            sa.ForeignKey("document_comments.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("state", sa.String(16), server_default="open", nullable=False),
        sa.Column("reply_count", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_document_thread_revision"),
        sa.CheckConstraint("reply_count >= 0", name="ck_document_thread_reply_count"),
        sa.CheckConstraint(
            "state IN ('open', 'resolved')", name="ck_document_thread_state"
        ),
    )

    # A room with comments and no document row: made empty, as reading its
    # document would have made it.
    op.execute("""
        INSERT INTO documents
            (id, project_id, room_id, kind, content, version, author,
             created_at, updated_at)
        SELECT gen_random_uuid(), t.project_id, t.id, 'doc', '', 0, 'system',
               now(), now()
        FROM topics t
        WHERE EXISTS (
                SELECT 1 FROM blocks c WHERE c.kind = 'comment' AND c.topic_id = t.id
            )
          AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.room_id = t.id)
    """)

    # Where each comment block sits: the thread it belongs to, the number it
    # carries there, and whether it opens one. `root` walks an early reply's
    # `reply_to` chain up to the comment that opened its thread.
    op.execute("""
        CREATE TEMPORARY TABLE comment_place ON COMMIT DROP AS
        WITH RECURSIVE
        comment AS (
            SELECT c.id, c.reply_to, c.created_at,
                   r.comment_id AS numbered_in, r.sequence
            FROM blocks c
            LEFT JOIN doc_comment_replies r ON r.block_id = c.id
            WHERE c.kind = 'comment'
        ),
        opening AS (
            SELECT c.id
            FROM comment c
            WHERE c.numbered_in IS NULL
              AND NOT EXISTS (SELECT 1 FROM comment p WHERE p.id = c.reply_to)
        ),
        root AS (
            SELECT o.id, o.id AS root FROM opening o
            UNION ALL
            SELECT c.id, root.root
            FROM comment c
            JOIN root ON c.reply_to = root.id
            WHERE c.id NOT IN (SELECT id FROM opening)
        ),
        late AS (
            SELECT root.id, root.root,
                   COALESCE((SELECT max(r.sequence) FROM doc_comment_replies r
                             WHERE r.comment_id = root.root), 0)
                   + row_number() OVER (
                       PARTITION BY root.root ORDER BY c.created_at, c.id
                   ) AS sequence
            FROM root JOIN comment c ON c.id = root.id
            WHERE root.id <> root.root AND c.numbered_in IS NULL
        )
        SELECT o.id, NULL::uuid AS thread_id, NULL::integer AS sequence
        FROM opening o
        UNION ALL
        SELECT c.id, c.numbered_in, c.sequence
        FROM comment c WHERE c.numbered_in IS NOT NULL
        UNION ALL
        SELECT l.id, l.root, l.sequence::integer FROM late l
    """)

    op.execute("""
        INSERT INTO document_comments
            (id, document_id, thread_id, sequence, author, content,
             anchor_quote, created_at)
        SELECT b.id, d.id, p.thread_id, p.sequence, b.author, b.content,
               CASE WHEN p.thread_id IS NULL THEN b.anchor_quote END,
               b.created_at
        FROM comment_place p
        JOIN blocks b ON b.id = p.id
        JOIN documents d ON d.room_id = b.topic_id
        ORDER BY p.thread_id NULLS FIRST
    """)
    op.execute("""
        INSERT INTO document_threads (id, revision, state, reply_count)
        SELECT o.id,
               COALESCE(t.revision, 1),
               COALESCE(t.state, 'open'),
               (SELECT count(*) FROM document_comments r WHERE r.thread_id = o.id)
        FROM document_comments o
        LEFT JOIN doc_comment_threads t ON t.comment_id = o.id
        WHERE o.thread_id IS NULL
    """)

    op.drop_table("doc_comment_replies")
    op.drop_table("doc_comment_threads")
    op.execute("DELETE FROM blocks WHERE kind = 'comment'")
    op.drop_column("blocks", "anchor_quote")

    op.execute(
        "CREATE INDEX ix_document_comments_search ON document_comments "
        "USING bm25 (id, content, document_id) "
        "WITH (key_field = 'id', text_fields = "
        f"'{_text_fields(['content'], ['document_id'])}')"
    )


def downgrade() -> None:
    raise RuntimeError(
        "irreversible: the comment blocks were deleted once copied. Restore from "
        "a backup taken before 4383bf20b465 if needed."
    )
