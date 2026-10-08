"""Documents get their own table; the living-doc tables hang off it

A room's living document was a `blocks` row (`kind = doc`, the room's main
line) with its top-level blocks as `kind = doc_node` rows under it, and the
journal tables (`living_doc_*`) were keyed by room. A document now belongs to
a project, and a room's living document is the one whose `room_id` is that
room (#2423):

- `documents`: one row per room document, **with the id its doc block had**.
  Version history, conversation events (`refs`) and exported file names name
  that id, so none of them changes.
- `document_nodes`: the room documents' `doc_node` blocks, same ids and
  authors (who wrote a passage is accumulated, not derivable from the text).
- `living_doc_versions` / `_states` / `_operations` / `_locks` become
  `document_*`, keyed by the document. Lock rows are mutexes and are not
  carried over. Versions only lose their `room_id`: no history row is
  rewritten, which the immutability trigger would refuse anyway.
- Each document whose current version has no history row (it predates the
  journal) gets that row, so history always reaches the current version.
- The three journal triggers are recreated on the new tables; the history
  trigger now asks whether the document is gone, not its block.
- Old thread briefs (`kind = doc` with a `task_id`) were readable nowhere: a
  thread's brief has been the card's `tasks.brief` since. Their text goes
  into the card's brief where that is empty; the blocks, their nodes and
  their history go.
- `blocks` loses the columns only documents used: `doc_version`,
  `struct_parent`, `node_type`, `struct_order`. The `doc` and `doc_node`
  labels stay in the `blockkind` enum, unused: Postgres cannot drop an enum
  label without rewriting every block row.
- BM25 indexes on `documents` and `document_nodes` keep both searchable.

Irreversible: the doc blocks are deleted. Restore from a backup taken before
this revision.

Revision ID: 41a261d02e9e
Revises: 08b4bcff4ad2
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "41a261d02e9e"
down_revision: str | Sequence[str] | None = "08b4bcff4ad2"
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


def _drop_journal_triggers() -> None:
    op.execute("DROP TRIGGER living_doc_receipt_immutable ON living_doc_operations")
    op.execute("DROP FUNCTION living_doc_receipt_immutable()")
    op.execute("DROP TRIGGER living_doc_receipt_complete ON living_doc_operations")
    op.execute("DROP FUNCTION living_doc_receipt_complete()")
    op.execute("DROP TRIGGER living_doc_history_immutable ON living_doc_versions")
    op.execute("DROP FUNCTION living_doc_history_immutable()")


def _create_journal_triggers() -> None:
    op.execute("""
        CREATE FUNCTION document_history_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' AND NOT EXISTS (
                SELECT 1 FROM documents WHERE id = OLD.document_id
            ) THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'document history is immutable'
                USING ERRCODE = '23514';
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER document_history_immutable
        BEFORE UPDATE OR DELETE ON document_versions
        FOR EACH ROW EXECUTE FUNCTION document_history_immutable()
    """)
    op.execute("""
        CREATE FUNCTION document_receipt_complete() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM document_operations
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
        CREATE CONSTRAINT TRIGGER document_receipt_complete
        AFTER INSERT OR UPDATE ON document_operations
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION document_receipt_complete()
    """)
    op.execute("""
        CREATE FUNCTION document_receipt_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.receipt IS NOT NULL AND OLD.receipt::text <> 'null' THEN
                RAISE EXCEPTION 'document receipt is immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF (NEW.document_id, NEW.actor, NEW.action, NEW.operation_id,
                NEW.fingerprint)
                IS DISTINCT FROM
                (OLD.document_id, OLD.actor, OLD.action, OLD.operation_id,
                 OLD.fingerprint) THEN
                RAISE EXCEPTION 'document claim identity is immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER document_receipt_immutable
        BEFORE UPDATE ON document_operations
        FOR EACH ROW EXECUTE FUNCTION document_receipt_immutable()
    """)


def upgrade() -> None:
    # The backend this deploy replaces is still serving while this runs. That
    # includes the tables the foreign keys below point at, since adding or
    # dropping one locks its target too.
    with_lock_retries(
        "topics, projects, living_doc_locks, blocks, living_doc_versions,"
        " living_doc_operations, living_doc_states, tasks",
        nowait=True,
    )
    # The triggers would refuse the backfills below; they come back at the end,
    # on the new tables.
    _drop_journal_triggers()

    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "room_id",
            sa.Uuid(),
            sa.ForeignKey("topics.id", ondelete="CASCADE"),
            nullable=True,
            unique=True,
        ),
        sa.Column("kind", sa.String(16), nullable=False, server_default="doc"),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("author", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_documents_project_id", "documents", ["project_id"])
    op.create_table(
        "document_nodes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_type", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("position", sa.Float(), nullable=False),
        sa.Column("author", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_document_nodes_document_id", "document_nodes", ["document_id"])

    # A room's document: its oldest main-line doc block, as the code read it.
    op.execute("""
        INSERT INTO documents
            (id, project_id, room_id, kind, content, version, author,
             created_at, updated_at)
        SELECT DISTINCT ON (b.topic_id)
            b.id, b.project_id, b.topic_id, 'doc', b.content, b.doc_version,
            b.author, b.created_at, b.updated_at
        FROM blocks b
        WHERE b.kind = 'doc' AND b.task_id IS NULL
        ORDER BY b.topic_id, b.created_at
    """)
    # A room whose live state or write receipts exist without a document
    # block: an empty document (version 0) holds them.
    op.execute("""
        INSERT INTO documents
            (id, project_id, room_id, kind, content, version, author,
             created_at, updated_at)
        SELECT gen_random_uuid(), t.project_id, t.id, 'doc', '', 0, 'system',
               now(), now()
        FROM topics t
        WHERE t.id IN (
                SELECT room_id FROM living_doc_states
                UNION SELECT room_id FROM living_doc_operations
            )
          AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.room_id = t.id)
    """)
    op.execute("""
        INSERT INTO document_nodes
            (id, document_id, node_type, content, position, author, created_at)
        SELECT n.id, d.id, coalesce(n.node_type, 'paragraph'), n.content,
               coalesce(n.struct_order, 0), n.author, n.created_at
        FROM blocks n
        JOIN documents d ON d.room_id = n.topic_id
        WHERE n.kind = 'doc_node' AND n.task_id IS NULL
    """)

    # Old thread briefs: the text goes to the card that has no brief.
    op.execute("""
        UPDATE tasks t
        SET brief = b.content
        FROM (
            SELECT DISTINCT ON (task_id) task_id, content
            FROM blocks
            WHERE kind = 'doc' AND task_id IS NOT NULL
            ORDER BY task_id, created_at
        ) b
        WHERE b.task_id = t.id AND coalesce(t.brief, '') = ''
    """)
    # Every doc block that did not become a document goes now, with the
    # history that still points at it through the blocks foreign key.
    op.execute("""
        DELETE FROM blocks
        WHERE kind = 'doc' AND id NOT IN (SELECT id FROM documents)
    """)
    op.execute("DELETE FROM blocks WHERE kind = 'doc_node'")

    # Versions: every row left names a document by its old block id.
    op.drop_constraint(
        "living_doc_versions_document_id_fkey", "living_doc_versions", "foreignkey"
    )
    op.drop_index("ix_living_doc_versions_room_id", table_name="living_doc_versions")
    op.drop_column("living_doc_versions", "room_id")
    op.rename_table("living_doc_versions", "document_versions")
    op.execute(
        "ALTER TABLE document_versions "
        "RENAME CONSTRAINT living_doc_versions_pkey TO document_versions_pkey"
    )
    op.execute(
        "ALTER TABLE document_versions "
        "RENAME CONSTRAINT uq_living_doc_version TO uq_document_version"
    )
    op.create_foreign_key(
        "document_versions_document_id_fkey",
        "document_versions",
        "documents",
        ["document_id"],
        ["id"],
        ondelete="CASCADE",
    )
    # The doc blocks that became documents can go now that history no longer
    # points at them.
    op.execute("DELETE FROM blocks WHERE kind = 'doc'")
    op.execute("""
        INSERT INTO document_versions
            (id, document_id, version, content, content_hash, previous_version,
             base_version, actor, created_at)
        SELECT gen_random_uuid(), d.id, d.version, d.content,
               encode(sha256(convert_to(d.content, 'UTF8')), 'hex'),
               CASE WHEN d.version > 1 THEN d.version - 1 END, NULL, d.author,
               d.updated_at
        FROM documents d
        WHERE d.version > 0
          AND NOT EXISTS (
              SELECT 1 FROM document_versions v
              WHERE v.document_id = d.id AND v.version = d.version
          )
    """)

    # States: keyed by the room's document.
    op.rename_table("living_doc_states", "document_states")
    op.add_column("document_states", sa.Column("document_id", sa.Uuid(), nullable=True))
    op.execute("""
        UPDATE document_states s SET document_id = d.id
        FROM documents d WHERE d.room_id = s.room_id
    """)
    op.execute("ALTER TABLE document_states DROP CONSTRAINT living_doc_states_pkey")
    op.drop_column("document_states", "room_id")
    op.alter_column("document_states", "document_id", nullable=False)
    op.create_primary_key("document_states_pkey", "document_states", ["document_id"])
    op.create_foreign_key(
        "document_states_document_id_fkey",
        "document_states",
        "documents",
        ["document_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Operations: the same, with the receipt identity keyed by the document.
    op.rename_table("living_doc_operations", "document_operations")
    op.execute(
        "ALTER TABLE document_operations "
        "RENAME CONSTRAINT living_doc_operations_pkey TO document_operations_pkey"
    )
    op.add_column(
        "document_operations", sa.Column("document_id", sa.Uuid(), nullable=True)
    )
    op.execute("""
        UPDATE document_operations o SET document_id = d.id
        FROM documents d WHERE d.room_id = o.room_id
    """)
    op.drop_constraint("uq_living_doc_operation", "document_operations", "unique")
    op.drop_index("ix_living_doc_operations_room_id", table_name="document_operations")
    op.drop_column("document_operations", "room_id")
    op.alter_column("document_operations", "document_id", nullable=False)
    op.create_unique_constraint(
        "uq_document_operation",
        "document_operations",
        ["document_id", "actor", "action", "operation_id"],
    )
    op.create_index(
        "ix_document_operations_document_id", "document_operations", ["document_id"]
    )
    op.create_foreign_key(
        "document_operations_document_id_fkey",
        "document_operations",
        "documents",
        ["document_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Locks are mutexes: nothing in them to carry over.
    op.drop_table("living_doc_locks")
    op.create_table(
        "document_locks",
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    _create_journal_triggers()

    op.drop_index("ix_blocks_struct_parent", table_name="blocks")
    op.drop_column("blocks", "struct_parent")
    op.drop_column("blocks", "node_type")
    op.drop_column("blocks", "struct_order")
    op.drop_column("blocks", "doc_version")

    op.execute(
        "CREATE INDEX ix_documents_search ON documents USING bm25 (id, content) "
        f"WITH (key_field = 'id', text_fields = '{_text_fields(['content'], [])}')"
    )
    op.execute(
        "CREATE INDEX ix_document_nodes_search ON document_nodes "
        "USING bm25 (id, content, document_id) "
        "WITH (key_field = 'id', text_fields = "
        f"'{_text_fields(['content'], ['document_id'])}')"
    )


def downgrade() -> None:
    raise RuntimeError(
        "irreversible: the doc and doc_node blocks were deleted and old thread "
        "briefs were folded into their cards. Restore from a backup taken "
        "before 41a261d02e9e if needed."
    )
