"""Back up project memories, verify their documents, and retire the pool.

Revision ID: e7b2c49d8106
Revises: a1c4e8f30b26
Create Date: 2026-09-22

Deploy a1c4e8f30b26 before this revision: it removes the pool's writers.
Retired facts remain in an immutable backup, never in the live document.
The backup includes every column of every deleted row, including live rows.
"""

import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from alembic import op

revision = "e7b2c49d8106"
down_revision = "a1c4e8f30b26"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")


def backup_rows(rows: list[dict]) -> Path:
    # Production HOME is the persistent /data/apphome volume, also used by
    # forge migration backups. A failed write must abort before any deletion.
    directory = Path.home() / "memory-migration" / revision
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = (json.dumps(rows, ensure_ascii=False, sort_keys=True) + "\n").encode()
    digest = hashlib.sha256(payload).hexdigest()
    path = directory / f"{digest}.json"
    if not path.exists():
        with path.open("xb") as stream:
            os.chmod(path, 0o600)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    if path.read_bytes() != payload:
        raise RuntimeError(f"Memory backup does not match the source rows: {path}")
    log.info("%s backup rows=%s sha256=%s path=%s", revision, len(rows), digest, path)
    return path


def copy_live_rows(bind: sa.Connection) -> None:
    path = Path(__file__).with_name(
        "a1c4e8f30b26_project_memory_becomes_the_overview_document.py"
    )
    spec = importlib.util.spec_from_file_location("_project_memory_copy", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bind.execute(sa.text(module.SEED_OVERVIEW_DOC))
    bind.execute(sa.text(module.APPEND_PROJECT_MEMORY_TO_OVERVIEW))


async def sync_document_nodes(connection: AsyncConnection, ids: list) -> None:
    from app.domain.block.models import Block
    from app.domain.topic.services import TopicService

    # Use the migration transaction and the existing reconciler: unchanged
    # paragraph IDs (and their comment anchors) must survive the second copy.
    async with AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as session:
        service = TopicService(session)
        for block_id in ids:
            root = await session.get(Block, block_id)
            assert root is not None
            await service._sync_doc_nodes(root, root.content)
        await session.commit()


def upgrade() -> None:
    bind = op.get_bind()
    # Keep the backed-up set stable until deletion, including against an old
    # process that was accidentally left running after the first deployment.
    bind.execute(sa.text("LOCK TABLE memory_entries IN SHARE ROW EXCLUSIVE MODE"))
    rows = list(
        bind.execute(
            sa.text(
                "SELECT row_to_json(m) FROM memory_entries m "
                "WHERE scope = 'project' ORDER BY id"
            )
        ).scalars()
    )
    if not rows:
        return
    backup_rows(rows)
    copy_live_rows(bind)
    targets = {
        str(row.project_id): row
        for row in bind.execute(
            sa.text(
                "SELECT p.id AS project_id, b.id, b.content "
                "FROM projects p JOIN blocks b ON b.id = ("
                "SELECT doc.id FROM blocks doc WHERE doc.topic_id = p.root_topic_id "
                "AND doc.task_id IS NULL AND doc.kind = 'doc' "
                "ORDER BY doc.created_at LIMIT 1) "
                "WHERE EXISTS (SELECT 1 FROM memory_entries m "
                "WHERE m.scope = 'project' AND m.scope_id = p.id::text "
                "AND m.retired_at IS NULL) FOR UPDATE OF b"
            )
        )
    }
    failed = []
    documents = set()
    for row in rows:
        digest = hashlib.sha256(row["content"].encode()).hexdigest()
        if row["retired_at"] is not None:
            log.info(
                "%s row=%s status=backed-up-retired sha256=%s",
                revision,
                row["id"],
                digest,
            )
            continue
        target = targets.get(row["scope_id"])
        offset = target.content.find(row["content"]) if target is not None else -1
        copied = (
            target.content[offset : offset + len(row["content"])]
            if offset >= 0
            else None
        )
        if copied is None or hashlib.sha256(copied.encode()).hexdigest() != digest:
            failed.append(row["id"])
            log.error(
                "%s row=%s status=unmatched sha256=%s", revision, row["id"], digest
            )
        else:
            documents.add(target.id)
            log.info("%s row=%s status=verified sha256=%s", revision, row["id"], digest)
    if failed:
        raise RuntimeError(
            "Project memories missing from their documents: " + ", ".join(failed)
        )
    op.run_async(sync_document_nodes, sorted(documents, key=str))
    deleted = bind.execute(
        sa.text("DELETE FROM memory_entries WHERE scope = 'project'")
    )
    log.info(
        "%s status=complete deleted=%s documents=%s",
        revision,
        deleted.rowcount,
        len(documents),
    )


def downgrade() -> None:
    raise RuntimeError(
        "Restore project memories from the migration backup before downgrading."
    )
