"""Run the real forward migration on populated isolated PostgreSQL tables."""

import asyncio
import importlib.util
import uuid
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


def test_forward_migration_preserves_legacy_anchor_without_invented_range(client):
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/b84d0f9ac721_doc_comment_threads.py"
    )
    spec = importlib.util.spec_from_file_location("comment_thread_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    async def verify():
        async with client.test_factory() as session:
            conn = await session.connection()
            schema = "comment_migration_" + uuid.uuid4().hex
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            await conn.execute(
                text("""
                CREATE TABLE blocks (
                    id uuid PRIMARY KEY, kind text NOT NULL, task_id uuid,
                    reply_to uuid REFERENCES blocks(id) ON DELETE SET NULL,
                    struct_parent uuid, content text, anchor_quote text
                )
            """)
            )
            node, root, orphan, reply = [uuid.uuid4() for _ in range(4)]
            document = uuid.uuid4()
            await conn.execute(
                text("""
                INSERT INTO blocks(id,kind,struct_parent,content)
                VALUES (:id,'doc_node',:doc,'😀同句')
            """),
                {"id": node, "doc": document},
            )
            await conn.execute(
                text("""
                INSERT INTO blocks(id,kind,reply_to,content,anchor_quote)
                VALUES (:id,'comment',:parent,'old comment','同句')
            """),
                [
                    {"id": root, "parent": node},
                    {"id": orphan, "parent": None},
                    {"id": reply, "parent": root},
                ],
            )

            def upgrade(sync_conn):
                migration.op = Operations(MigrationContext.configure(sync_conn))
                migration.upgrade()

            await conn.run_sync(upgrade)
            rows = (
                await conn.execute(
                    text("SELECT comment_id, anchor FROM doc_comment_threads")
                )
            ).all()
            assert {row.comment_id for row in rows} == {root, orphan}
            anchor = next(row.anchor for row in rows if row.comment_id == root)
            assert anchor["node_id"] == str(node) and anchor["node_content"] == "😀同句"
            assert anchor["document_id"] == str(document) and anchor["quote"] == "同句"
            assert (
                anchor["base_version"] is None
                and anchor["start"] is None
                and anchor["end"] is None
            )
            await conn.execute(text("DELETE FROM blocks WHERE id=:id"), {"id": node})
            assert (
                await conn.scalar(
                    text("SELECT reply_to FROM blocks WHERE id=:id"), {"id": root}
                )
                is None
            )
            assert (
                await conn.scalar(
                    text("SELECT anchor FROM doc_comment_threads WHERE comment_id=:id"),
                    {"id": root},
                )
                == anchor
            )
            try:
                async with conn.begin_nested():
                    await conn.execute(
                        text(
                            "UPDATE doc_comment_threads SET anchor='{}' "
                            "WHERE comment_id=:id"
                        ),
                        {"id": root},
                    )
            except DBAPIError as exc:
                assert "historical evidence" in str(exc)
            else:
                raise AssertionError("anchor mutation was accepted")
            await conn.execute(
                text(
                    "UPDATE doc_comment_threads SET state='resolved',revision=2 "
                    "WHERE comment_id=:id"
                ),
                {"id": root},
            )
            assert (
                await conn.scalar(
                    text("SELECT content FROM blocks WHERE id=:id"), {"id": root}
                )
                == "old comment"
            )

            def downgrade(sync_conn):
                migration.op = Operations(MigrationContext.configure(sync_conn))
                migration.downgrade()

            await conn.run_sync(downgrade)
            assert await conn.scalar(text("SELECT count(*) FROM blocks")) == 3
            await session.rollback()  # also removes the test-only schema

    asyncio.run(verify())
