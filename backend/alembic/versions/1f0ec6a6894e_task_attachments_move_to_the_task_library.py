"""A 题目's attachments move into its library, and it starts at version 1

Revision ID: 1f0ec6a6894e
Revises: 0f147393ad6a
Create Date: 2026-10-24

#4767 §一. A 题目's files were rows of ``attachment`` linked to it by
``task_attachment``, with their bytes in the old storage (``app.core.storage``,
key in ``attachment.meta.storageKey``). From here they are files of the
题目's own library (``library_files.task_id``), bytes in the blob store, the
same records a project's library keeps.

For each 题目 that has no version yet:

- version 1 is recorded, as published by its publisher when it was published;
- its 题目说明 (``题目说明.md``) for version 1 is written into its library, the
  file a project taken from it reads under ``题目/``;
- each attachment still on it is copied in under its file name (a name already
  taken gets the next ``(n)``, the way the library names a second upload), as
  added by whoever uploaded it, when it was attached.

A 题目 with a version is done, so running this again does nothing to it.

Then the ``attachment`` rows that were a 题目's live attachments stop being
served: their bytes are the 题目's files now, read under the 题目's own rule
(publisher, space admin, taker). Left in ``attachment``, the generic
``GET /attachments/{id}`` would hand an image among them to any signed-in
person — that route lets images through for everyone, and the ids are
sequential. So those rows, and every ``task_attachment`` row naming them, are
copied into ``task_material_attachments_1f0ec6a6894e`` (the record of what was
there) and deleted. Their bytes in the old storage are not touched.
``task_attachment`` itself stays until the next release drops it: the release
this deploy replaces still reads it while this runs, and finds nothing.

The bytes move outside the database, so this reads the storage settings of the
backend it runs beside; see the two imports below.
"""

import asyncio
import hashlib
import json
import re
import uuid
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = "1f0ec6a6894e"
down_revision: str | Sequence[str] | None = "0f147393ad6a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: ``app.domain.task.package.BRIEF`` as of this revision.
BRIEF = "题目说明.md"

#: ``app.domain.task.package._ENTRY_KINDS`` as of this revision.
_ENTRY_KINDS = {0: "文字", 1: "文件"}


def _brief(task, entries, rubric) -> str:
    """``app.domain.task.package.brief`` as of this revision, for version 1."""
    lines = [f"# {task.name}", "", "第 1 版", ""]
    if task.intro:
        lines += ["## 简介", "", task.intro, ""]
    if task.description:
        lines += ["## 说明", "", task.description, ""]
    if entries:
        lines += ["## 提交内容", ""]
        lines += [
            f"{i}. {entry.description}（{_ENTRY_KINDS.get(entry.type, '文字')}）"
            for i, entry in enumerate(entries, start=1)
        ]
        lines.append("")
    if task.rubric_public and rubric:
        lines += ["## 评分要点", ""]
        lines += [
            f"- {row.item}（{row.points} 分）"
            + (f"：{row.criteria}" if row.criteria else "")
            for row in rubric
        ]
        lines.append("")
    return "\n".join(lines)


def _clean_upload_name(filename: str | None) -> str:
    """``app.domain.library.service.clean_upload_name`` as of this revision."""
    name = (filename or "file").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "_", name).strip().strip(".") or "file"
    return name.encode("utf-8")[:180].decode("utf-8", errors="ignore")


def _next_name(name: str, attempt: int) -> str:
    """``app.domain.library.service.next_name`` as of this revision."""
    if attempt == 1:
        return name
    folder, slash, leaf = name.rpartition("/")
    stem, dot, ext = leaf.rpartition(".")
    numbered = f"{stem}({attempt}).{ext}" if dot else f"{leaf}({attempt})"
    return f"{folder}{slash}{numbered}"


def _free_name(wanted: str, taken: set[str]) -> str:
    attempt = 1
    while _next_name(wanted, attempt) in taken:
        attempt += 1
    return _next_name(wanted, attempt)


def _move_bytes(
    briefs: dict[str, bytes], copies: dict[str, str]
) -> dict[str, tuple[int, str]]:
    """Write the briefs and copy each attachment (blob key -> old storage key)
    into the store new library bytes go to; the size and sha256 of what was
    written, by blob key. An attachment whose bytes are gone from the old
    storage is left out. One file is in memory at a time."""
    # migration-safety: allow app-import — the bytes are in the storage the running backend is configured with (environment and .env); a copied default would read and write somewhere else and move nothing
    from app.core import blob_store

    # migration-safety: allow app-import — the old attachment bytes are in the storage the running backend is configured with; a copied backend would look somewhere else and copy nothing
    from app.core.storage import get_storage_backend

    async def go() -> dict[str, tuple[int, str]]:
        store = blob_store.for_new()
        old = get_storage_backend()
        written: dict[str, tuple[int, str]] = {}
        for key, data in briefs.items():
            await store.put(key, data)
            written[key] = (len(data), hashlib.sha256(data).hexdigest())
        for key, storage_key in copies.items():
            data = await old.download(storage_key)
            if data is None:
                print(f"task attachment bytes missing at {storage_key}; not copied")
                continue
            await store.put(key, data)
            written[key] = (len(data), hashlib.sha256(data).hexdigest())
        return written

    # The migration runs inside alembic's own event loop (``env.py``); the
    # storage calls are coroutines, so they get a loop of their own.
    with ThreadPoolExecutor(1) as pool:
        return pool.submit(asyncio.run, go()).result()


def _location() -> str:
    # migration-safety: allow app-import — new bytes go to the store the running backend's settings name (environment and .env); the record has to say the same place
    from app.core.config import settings

    return settings.files_store


#: Where the retired attachment rows and their links are kept.
BACKUP = "task_material_attachments_1f0ec6a6894e"


def _retire_attachment_rows() -> None:
    """The ``attachment`` rows a live ``task_attachment`` names — on any 题目,
    a deleted one included, since the generic route gated those too — kept in
    ``BACKUP`` with the links that named them, then deleted, links first (the
    links hold the foreign key)."""
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {BACKUP} (
            attachment_id integer NOT NULL,
            type varchar(20) NOT NULL,
            url text NOT NULL,
            meta jsonb NOT NULL,
            link_id bigint NOT NULL,
            task_id bigint NOT NULL,
            link_deleted_at timestamptz,
            retired_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute(f"""
        INSERT INTO {BACKUP}
            (attachment_id, type, url, meta, link_id, task_id, link_deleted_at)
        SELECT a.id, a.type, a.url, a.meta::jsonb, ta.id, ta.task_id, ta.deleted_at
        FROM attachment a
        JOIN task_attachment ta ON ta.attachment_id = a.id
        WHERE a.id IN (
            SELECT attachment_id FROM task_attachment WHERE deleted_at IS NULL
        )
    """)
    op.execute(f"""
        DELETE FROM task_attachment
        WHERE attachment_id IN (SELECT attachment_id FROM {BACKUP})
    """)
    op.execute(f"""
        DELETE FROM attachment
        WHERE id IN (SELECT attachment_id FROM {BACKUP})
    """)


def upgrade() -> None:
    _copy_into_libraries()
    _retire_attachment_rows()


def _copy_into_libraries() -> None:
    bind = op.get_bind()
    tasks = list(
        bind.execute(
            sa.text("""
                SELECT t.id, t.name, t.intro, t.description, t.rubric_public,
                       t.creator_id, COALESCE(t.published_at, t.created_at) AS at,
                       u.username
                FROM task t LEFT JOIN "user" u ON u.id = t.creator_id
                WHERE t.deleted_at IS NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM task_package_revisions r WHERE r.task_id = t.id
                  )
                ORDER BY t.id
            """)
        )
    )
    if not tasks:
        return
    ids = [task.id for task in tasks]
    entries: dict[int, list] = {}
    for row in bind.execute(
        sa.text(
            "SELECT task_id, description, type FROM task_submission_schema"
            " WHERE task_id = ANY(:ids) ORDER BY task_id, index"
        ),
        {"ids": ids},
    ):
        entries.setdefault(row.task_id, []).append(row)
    rubric: dict[int, list] = {}
    for row in bind.execute(
        sa.text(
            "SELECT task_id, item, points, criteria FROM task_rubric_item"
            " WHERE task_id = ANY(:ids) ORDER BY task_id, index"
        ),
        {"ids": ids},
    ):
        rubric.setdefault(row.task_id, []).append(row)
    links = list(
        bind.execute(
            sa.text("""
                SELECT ta.task_id, ta.created_at, a.meta, u.username
                FROM task_attachment ta
                JOIN attachment a ON a.id = ta.attachment_id
                LEFT JOIN "user" u
                  ON u.id = CASE WHEN (a.meta::jsonb ->> 'uploaderId') ~ '^[0-9]+$'
                                 THEN (a.meta::jsonb ->> 'uploaderId')::int END
                WHERE ta.deleted_at IS NULL AND ta.task_id = ANY(:ids)
                ORDER BY ta.task_id, ta.id
            """),
            {"ids": ids},
        )
    )

    location = _location()
    now = datetime.now(UTC)
    rows: list[dict] = []
    briefs: dict[str, bytes] = {}
    for task in tasks:
        record_id = uuid.uuid4()
        key = f".library-blobs/task-{task.id}/{record_id}"
        briefs[key] = _brief(
            task, entries.get(task.id, []), rubric.get(task.id, [])
        ).encode()
        rows.append(
            {
                "id": record_id,
                "task_id": task.id,
                "name": BRIEF,
                "key": key,
                "by": task.username,
                "at": now,
            }
        )
    taken: dict[int, set[str]] = {task.id: {BRIEF} for task in tasks}
    copies: dict[str, str] = {}
    pending: list[dict] = []
    for link in links:
        meta = link.meta if isinstance(link.meta, dict) else json.loads(link.meta)
        storage_key = meta.get("storageKey")
        if not storage_key:
            continue
        name = _free_name(_clean_upload_name(meta.get("filename")), taken[link.task_id])
        taken[link.task_id].add(name)
        record_id = uuid.uuid4()
        key = f".library-blobs/task-{link.task_id}/{record_id}"
        copies[key] = storage_key
        pending.append(
            {
                "id": record_id,
                "task_id": link.task_id,
                "name": name,
                "key": key,
                "by": link.username,
                "at": link.created_at,
            }
        )
    written = _move_bytes(briefs, copies)
    rows += [row for row in pending if row["key"] in written]

    for task in tasks:
        bind.execute(
            sa.text(
                "INSERT INTO task_package_revisions"
                " (task_id, number, author_id, changes, created_at)"
                " VALUES (:task, 1, :by, CAST(:changes AS jsonb), :at)"
            ),
            {
                "task": task.id,
                "by": task.creator_id,
                "changes": json.dumps({"published": True}),
                "at": task.at,
            },
        )
    for row in rows:
        size, sha = written[row["key"]]
        bind.execute(
            sa.text("""
                INSERT INTO library_files
                    (id, task_id, name, location, blob_key, bytes, sha256,
                     added_by, created_at)
                VALUES (:id, :task_id, :name, :location, :key, :bytes, :sha,
                        :by, :at)
            """),
            {
                **row,
                "location": location,
                "bytes": size,
                "sha": sha,
            },
        )


def downgrade() -> None:
    """The retired attachment rows and their links come back from ``BACKUP``.
    The library rows this wrote go with ``0f147393ad6a``'s downgrade, which
    drops the column and the table they are in; the bytes stay where they were
    written."""
    op.execute(f"""
        INSERT INTO attachment (id, type, url, meta)
        SELECT DISTINCT ON (attachment_id) attachment_id, type, url, meta::json
        FROM {BACKUP}
        ORDER BY attachment_id
        ON CONFLICT (id) DO NOTHING
    """)
    op.execute(f"""
        INSERT INTO task_attachment
            (id, task_id, attachment_id, download_count, created_at, updated_at,
             deleted_at)
        SELECT link_id, task_id, attachment_id, 0, retired_at, retired_at,
               link_deleted_at
        FROM {BACKUP}
        ON CONFLICT (id) DO NOTHING
    """)
    op.execute(f"DROP TABLE {BACKUP}")
