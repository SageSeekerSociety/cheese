"""0c800ff1db2f: the library lists its records.

Two things the page could not say before become rows: a file put in before the
records table, and who saved the files the rooms-become-tasks migrations copied
out of a room.
"""

import hashlib
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from tests.integration.migration_replay import database_at, seed_room

BEFORE = "9f2b7c14a8e3"
AFTER = "0c800ff1db2f"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_files_on_disk_get_rows_and_room_copies_name_who_saved_them(tmp_path):
    workspace = tmp_path / "workspace"
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        library = workspace / ".library" / str(project)

        def disk(name: str, data: bytes) -> Path:
            path = library / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return path

        # Put in before the records table, and attached in a message once.
        disk("旧合同.docx", b"PK-contract")
        sent_at = datetime(2026, 8, 1, tzinfo=UTC)
        db.execute(
            "INSERT INTO blocks (id, project_id, conversation_id, kind, author_type,"
            " author, content, refs, created_at, updated_at) VALUES ($1, $2, $3,"
            " 'attachment', 'participant', 'carol', 'library/旧合同.docx', '[]',"
            " $4, $4)",
            uuid.uuid4(),
            project,
            room,
            sent_at,
        )
        # Put in before the records table and never mentioned.
        quiet = disk("没人提过.txt", b"quiet")
        os.utime(quiet, (1_700_000_000, 1_700_000_000))
        # A replacement caught halfway is not a file of the library.
        disk(".没人提过.txt.0a1b2c", b"half")
        # Copied out of a room by the rooms-become-tasks migration, with nobody
        # as its source; the room's file history knows who saved it.
        disk("导出报表/办公成果/report.md", b"# report")
        db.execute(
            "INSERT INTO library_files (id, project_id, name, bytes, sha256,"
            " added_by, room_id, created_at) VALUES ($1, $2, $3, 8, $4, NULL, $5,"
            " now())",
            uuid.uuid4(),
            project,
            "导出报表/办公成果/report.md",
            _sha(b"# report"),
            room,
        )
        for seq, (author, data) in enumerate(
            [("bob", b"# draft"), ("alice", b"# report")], start=1
        ):
            db.execute(
                "INSERT INTO room_file_revisions (id, project_id, room_id, path,"
                " seq, sha256, size, author_handle, author_kind, source,"
                " created_at, updated_at) VALUES ($1, $2, $3, '办公成果/report.md',"
                " $4, $5, $6, $7, 'human', 'editor', now(), now())",
                uuid.uuid4(),
                project,
                room,
                seq,
                _sha(data),
                len(data),
                author,
            )
        # Recorded already: not recorded twice.
        disk("预算表.xlsx", b"budget")
        db.execute(
            "INSERT INTO library_files (id, project_id, name, bytes, sha256,"
            " added_by, created_at) VALUES ($1, $2, '预算表.xlsx', 6, $3, 'dave',"
            " now())",
            uuid.uuid4(),
            project,
            _sha(b"budget"),
        )

        db.upgrade(AFTER, env={"WORKSPACE_ROOT": str(workspace)})

        rows = {
            row["name"]: row
            for row in db.fetch(
                "SELECT name, bytes, sha256, added_by, room_id, created_at,"
                " location, blob_key FROM library_files WHERE project_id = $1",
                project,
            )
        }
        assert sorted(rows) == [
            "导出报表/办公成果/report.md",
            "旧合同.docx",
            "没人提过.txt",
            "预算表.xlsx",
        ]
        contract = rows["旧合同.docx"]
        assert (contract["added_by"], contract["room_id"]) == ("carol", room)
        assert contract["created_at"] == sent_at
        assert (contract["bytes"], contract["sha256"]) == (11, _sha(b"PK-contract"))
        unmentioned = rows["没人提过.txt"]
        assert unmentioned["added_by"] is None
        assert unmentioned["created_at"] == datetime.fromtimestamp(1_700_000_000, UTC)
        assert rows["导出报表/办公成果/report.md"]["added_by"] == "alice"
        assert rows["预算表.xlsx"]["added_by"] == "dave"
        # The bytes stay where they were; their keys are filled in later.
        assert {row["location"] for row in rows.values()} == {"local"}
        assert {row["blob_key"] for row in rows.values()} == {None}
