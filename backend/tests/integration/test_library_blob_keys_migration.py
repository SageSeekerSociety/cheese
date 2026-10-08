"""c47997681006 / 84a4c735db35: every library record names where its bytes are.

Rows written before ``blob_key`` existed get the place their bytes were
written to, and from then on the column cannot be empty.
"""

import uuid

import asyncpg
import pytest

from tests.integration.migration_replay import database_at, seed_room

BEFORE = "0c800ff1db2f"
AFTER = "84a4c735db35"


def test_old_rows_get_the_place_their_bytes_were_written_to():
    with database_at(BEFORE) as db:
        project, _ = seed_room(db)
        current, replaced, keyed = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        for row, name, superseded, key in (
            (current, "合同/报价.xlsx", False, None),
            (replaced, "合同/报价.xlsx", True, None),
            (keyed, "说明.md", False, f".library-blobs/{project}/x"),
        ):
            db.execute(
                "INSERT INTO library_files (id, project_id, name, bytes, sha256,"
                " location, blob_key, created_at, superseded_at) VALUES ($1, $2,"
                " $3, 1, '0', 'local', $4, now(),"
                " CASE WHEN $5 THEN now() END)",
                row,
                project,
                name,
                key,
                superseded,
            )

        db.upgrade(AFTER)

        keys = {
            row["id"]: row["blob_key"]
            for row in db.fetch("SELECT id, blob_key FROM library_files")
        }
        assert keys == {
            current: f".library/{project}/合同/报价.xlsx",
            replaced: f".library-history/{project}/{replaced}/报价.xlsx",
            keyed: f".library-blobs/{project}/x",
        }
        with pytest.raises(asyncpg.NotNullViolationError):
            db.execute(
                "INSERT INTO library_files (id, project_id, name, bytes, sha256,"
                " created_at) VALUES ($1, $2, 'n', 1, '0', now())",
                uuid.uuid4(),
                project,
            )
