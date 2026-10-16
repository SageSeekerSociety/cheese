"""What migration 5d0e8b3c7a21 does to what the library already holds (#3828).

The rules, stated before it was written:

- a file sent in a private chat or a private channel goes back to that
  conversation: the message that carried it still opens it, and the library no
  longer lists it;
- such a file that something outside its conversation refers to stays where
  that reference finds it;
- a file sent in a public channel stays in the library;
- a document 芝士 made in a private conversation goes back to it, one made in a
  public channel stays in the library and knows its channel, and one nobody
  made in a conversation stays the project's.
"""

import json
import uuid
from pathlib import Path

import pytest

from tests.integration.migration_replay import ReplayDatabase, database_at, seed_room

BEFORE = "49a2e86fa493"
AFTER = "5d0e8b3c7a21"


@pytest.fixture
def db():
    with database_at(BEFORE) as replay:
        yield replay


def _room(db: ReplayDatabase, project, *, dm=False, private=False) -> uuid.UUID:
    room = uuid.uuid4()
    db.execute(
        "INSERT INTO topics (id, project_id, title, kind, status, is_private,"
        " members_only, created_at, updated_at)"
        " VALUES ($1, $2, 'r', 'topic', 'active', $3, $4, now(), now())",
        room,
        project,
        dm,
        private,
    )
    return room


def _library_file(db, workspace: Path, project, room, name: str, data: bytes) -> None:
    record = uuid.uuid4()
    key = f".library-blobs/{project}/{record}"
    (workspace / key).parent.mkdir(parents=True, exist_ok=True)
    (workspace / key).write_bytes(data)
    db.execute(
        "INSERT INTO library_files (id, project_id, name, location, blob_key, bytes,"
        " sha256, added_by, room_id, created_at)"
        " VALUES ($1, $2, $3, 'local', $4, $5, 'x', 'alice', $6, now())",
        record,
        project,
        name,
        key,
        len(data),
        room,
    )


def _block(db, project, where, content: str, *, kind="attachment", meta=None):
    block = uuid.uuid4()
    db.execute(
        "INSERT INTO blocks (id, project_id, conversation_id, kind, author_type,"
        " author, content, refs, meta, created_at, updated_at)"
        " VALUES ($1, $2, $3, $4, $5, 'alice', $6, '[]', $7::json, now(), now())",
        block,
        project,
        where,
        kind,
        "platform" if meta else "participant",
        content,
        json.dumps(meta) if meta else None,
    )
    return block


def _document(db, project, number: int) -> uuid.UUID:
    doc = uuid.uuid4()
    db.execute(
        "INSERT INTO documents (id, project_id, kind, number, title, content,"
        " version, author, created_at, updated_at)"
        " VALUES ($1, $2, 'doc', $3, 't', '', 0, 'cheese', now(), now())",
        doc,
        project,
        number,
    )
    return doc


def _made_in(db, project, where, doc) -> None:
    _block(
        db,
        project,
        where,
        "芝士 新建了文档",
        kind="event",
        meta={"document": {"id": str(doc), "title": "t"}, "doc_created": True},
    )


def test_private_files_and_documents_go_back_to_their_conversations(db, tmp_path):
    project, public = seed_room(db)
    private = _room(db, project, private=True)
    dm = _room(db, project, dm=True)

    _library_file(db, tmp_path, project, private, "协议.docx", b"agreement")
    _block(db, project, private, "library/协议.docx")
    _library_file(db, tmp_path, project, dm, "共用.docx", b"shared")
    _block(db, project, dm, "library/共用.docx")
    _block(db, project, public, "library/共用.docx")
    _library_file(db, tmp_path, project, public, "预算.xlsx", b"budget")
    _block(db, project, public, "library/预算.xlsx")

    in_dm = _document(db, project, 1)
    _made_in(db, project, dm, in_dm)
    in_public = _document(db, project, 2)
    _made_in(db, project, public, in_public)
    by_hand = _document(db, project, 3)

    db.upgrade(AFTER, {"WORKSPACE_ROOT": str(tmp_path)})

    listed = {r["name"] for r in db.fetch("SELECT name FROM library_files")}
    assert listed == {"共用.docx", "预算.xlsx"}

    moved = db.fetchval(
        "SELECT content FROM blocks WHERE conversation_id = $1 AND kind = 'attachment'",
        private,
    )
    assert moved.startswith("uploads/") and moved.endswith("/协议.docx")
    room_copy = tmp_path / ".room-files" / str(project) / str(private) / moved
    assert room_copy.read_bytes() == b"agreement"

    rows = {
        r["id"]: (r["room_id"], r["kept_at"] is not None)
        for r in db.fetch("SELECT id, room_id, kept_at FROM documents")
    }
    assert rows[in_dm] == (dm, False)
    assert rows[in_public] == (public, True)
    assert rows[by_hand] == (None, False)
