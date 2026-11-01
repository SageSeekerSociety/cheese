"""A 题目's attachments move into its library, and it starts at version 1
(migration 1f0ec6a6894e).

The rules, as stated before the migration was written:

- every 题目 that has no version gets version 1, as published by its publisher;
- its library holds the 题目说明 of version 1 and a copy of each attachment
  still on it, with the bytes the attachment had, under its file name, as added
  by whoever uploaded it; one taken off the 题目 is not copied;
- a name already taken (by the 题目说明, or by an earlier attachment of the
  same name) takes the next ``(n)``;
- a deleted 题目 gets nothing;
- the ``attachment`` rows a 题目 still had are no longer there to be served by
  the generic attachment route (which lets any signed-in person read an image),
  kept in a record instead; one taken off a 题目 before is left as it was.
"""

import hashlib
import json
from pathlib import Path

from tests.integration.migration_replay import ReplayDatabase, database_at

BEFORE = "0f147393ad6a"
AFTER = "1f0ec6a6894e"


def _user(db: ReplayDatabase, user_id: int, name: str) -> None:
    db.execute(
        'INSERT INTO "user" (id, username, email, hashed_password, created_at,'
        " updated_at) VALUES ($1, $2, $3, 'x', now(), now())",
        user_id,
        name,
        f"{name}@example.com",
    )


def _task(db: ReplayDatabase, task_id: int, *, deleted: bool = False) -> None:
    db.execute(
        "INSERT INTO task (id, name, intro, description, creator_id, space_id,"
        " category_id, submitter_type, approved, default_deadline, resubmittable,"
        " editable, require_real_name, reject_reason, team_locking_policy,"
        " access_control_enabled, rubric_public, created_at, updated_at,"
        " deleted_at)"
        " VALUES ($1, '实验一', '排序', '实现快速排序', 501, 1, 1, 0, 0, 30,"
        " true, true, false, '', 'NO_LOCK', false, true, now(), now(),"
        " CASE WHEN $2 THEN now() END)",
        task_id,
        deleted,
    )
    db.execute(
        "INSERT INTO task_submission_schema (task_id, index, description, type)"
        " VALUES ($1, 0, '实验报告', 0)",
        task_id,
    )


def _attach(
    db: ReplayDatabase,
    uploads: Path,
    *,
    attachment_id: int,
    task_id: int,
    filename: str,
    data: bytes,
    removed: bool = False,
    kind: str = "file",
) -> None:
    key = f"files/{attachment_id}.bin"
    (uploads / "files").mkdir(parents=True, exist_ok=True)
    (uploads / key).write_bytes(data)
    meta = {"filename": filename, "storageKey": key, "uploaderId": 502}
    db.execute(
        "INSERT INTO attachment (id, type, url, meta) VALUES ($1, $4, $2, $3)",
        attachment_id,
        f"/uploads/{key}",
        json.dumps(meta),
        kind,
    )
    db.execute(
        "INSERT INTO task_attachment (id, task_id, attachment_id, download_count,"
        " created_at, updated_at, deleted_at)"
        " VALUES (nextval('task_attachment_seq'), $1, $2, 0, now(), now(),"
        " CASE WHEN $3 THEN now() END)",
        task_id,
        attachment_id,
        removed,
    )


def test_a_tasks_attachments_become_files_of_its_library(tmp_path: Path):
    uploads = tmp_path / "uploads"
    workspace = tmp_path / "workspace"
    with database_at(BEFORE) as db:
        _user(db, 501, "teacher")
        _user(db, 502, "assistant")
        _task(db, 7001)
        _task(db, 7002, deleted=True)
        _attach(
            db, uploads, attachment_id=91, task_id=7001, filename="数据.csv", data=b"a"
        )
        _attach(
            db, uploads, attachment_id=92, task_id=7001, filename="数据.csv", data=b"b"
        )
        _attach(
            db,
            uploads,
            attachment_id=93,
            task_id=7001,
            filename="题目说明.md",
            data=b"hand-written",
        )
        _attach(
            db,
            uploads,
            attachment_id=94,
            task_id=7001,
            filename="拿下来的.pdf",
            data=b"gone",
            removed=True,
        )
        _attach(
            db,
            uploads,
            attachment_id=95,
            task_id=7002,
            filename="删掉的题.pdf",
            data=b"x",
        )

        db.upgrade(
            AFTER,
            env={
                "STORAGE_TYPE": "local",
                "STORAGE_LOCAL_PATH": str(uploads),
                "WORKSPACE_ROOT": str(workspace),
                "FILES_STORE": "local",
            },
        )

        revisions = db.fetch(
            "SELECT task_id, number, author_id, changes FROM task_package_revisions"
            " WHERE task_id IN (7001, 7002)"
        )
        assert [(r["task_id"], r["number"], r["author_id"]) for r in revisions] == [
            (7001, 1, 501)
        ]
        assert json.loads(revisions[0]["changes"]) == {"published": True}

        rows = {
            r["name"]: r
            for r in db.fetch(
                "SELECT name, blob_key, location, bytes, sha256, added_by"
                " FROM library_files WHERE task_id = 7001"
            )
        }
        assert set(rows) == {"题目说明.md", "数据.csv", "数据(2).csv", "题目说明(2).md"}
        assert not db.fetch("SELECT 1 FROM library_files WHERE task_id = 7002")

        def stored(name: str) -> bytes:
            row = rows[name]
            assert row["location"] == "local"
            data = (workspace / row["blob_key"]).read_bytes()
            assert row["bytes"] == len(data)
            assert row["sha256"] == hashlib.sha256(data).hexdigest()
            return data

        assert stored("数据.csv") == b"a"
        assert stored("数据(2).csv") == b"b"
        assert stored("题目说明(2).md") == b"hand-written"
        assert rows["数据.csv"]["added_by"] == "assistant"
        brief = stored("题目说明.md").decode()
        assert "第 1 版" in brief
        assert "实现快速排序" in brief
        assert "实验报告" in brief


def test_a_tasks_attachment_rows_stop_being_served_by_the_generic_route(
    tmp_path: Path,
):
    """An image material uploaded the old way was an ``attachment`` row of type
    image, which ``GET /attachments/{id}`` hands any signed-in person. After the
    move it is the 题目's file, under the 题目's rule, and that row is gone."""
    uploads = tmp_path / "uploads"
    with database_at(BEFORE) as db:
        _user(db, 501, "teacher")
        _user(db, 502, "assistant")
        _task(db, 7101)
        _task(db, 7102, deleted=True)
        _attach(
            db,
            uploads,
            attachment_id=191,
            task_id=7101,
            filename="答案示意.png",
            data=b"\x89PNG",
            kind="image",
        )
        _attach(
            db,
            uploads,
            attachment_id=192,
            task_id=7102,
            filename="删掉的题.png",
            data=b"\x89PNG",
            kind="image",
        )
        _attach(
            db,
            uploads,
            attachment_id=193,
            task_id=7101,
            filename="早就拿下来的.png",
            data=b"\x89PNG",
            kind="image",
            removed=True,
        )

        db.upgrade(
            AFTER,
            env={
                "STORAGE_TYPE": "local",
                "STORAGE_LOCAL_PATH": str(uploads),
                "WORKSPACE_ROOT": str(tmp_path / "workspace"),
                "FILES_STORE": "local",
            },
        )

        left = {
            r["id"]
            for r in db.fetch("SELECT id FROM attachment WHERE id IN (191, 192, 193)")
        }
        assert left == {193}
        kept = db.fetch(
            "SELECT attachment_id, type, task_id FROM task_material_attachments_1f0ec6a6894e"
            " WHERE attachment_id IN (191, 192) ORDER BY attachment_id"
        )
        assert [(r["attachment_id"], r["type"], r["task_id"]) for r in kept] == [
            (191, "image", 7101),
            (192, "image", 7102),
        ]
        assert (uploads / "files" / "191.bin").read_bytes() == b"\x89PNG"

        # Going back puts the rows and their links back as they were.
        db.downgrade("0f147393ad6a")
        back = {
            r["id"]: r["type"]
            for r in db.fetch("SELECT id, type FROM attachment WHERE id IN (191, 192)")
        }
        assert back == {191: "image", 192: "image"}
        links = db.fetch(
            "SELECT attachment_id FROM task_attachment"
            " WHERE attachment_id IN (191, 192) AND deleted_at IS NULL"
        )
        assert sorted(r["attachment_id"] for r in links) == [191, 192]
