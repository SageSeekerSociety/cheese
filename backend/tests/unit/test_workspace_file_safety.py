"""File versions identify content rather than write time."""

import uuid

import pytest

from app.domain.workspace.forge_files import ProjectFiles
from app.domain.workspace.textfile import compare_bytes, content_version


def test_version_identifies_content_not_the_moment_it_was_written():
    assert content_version(b"abc") == content_version(b"abc")
    assert content_version(b"abc") != content_version(b"abd")


def test_comparison_distinguishes_binary_equality_and_final_newline():
    assert compare_bytes(b"a\x00", b"b\x00", "old", "new") == {
        "identical": False,
        "diff": None,
        "note": "binary",
    }
    assert compare_bytes(b"a\x00", b"a\x00", "old", "new")["identical"] is True
    change = compare_bytes(b"a", b"a\n", "old", "new")
    assert change["identical"] is False
    assert "No newline at end of file" in change["diff"]
    assert compare_bytes(b"x" * (1024 * 1024 + 1), b"x", "old", "new")["diff"] is None


@pytest.mark.anyio
async def test_two_delivered_trees_include_deletions_binary_and_mode_changes(
    monkeypatch,
):
    reader = ProjectFiles(None, uuid.uuid4(), None)

    def entry(path, oid, mode="100644"):
        return {"path": path, "oid": oid, "kind": "blob", "mode": mode, "bytes": 4}

    async def entries(revision):
        return {
            "delivered-first": [
                entry("gone.txt", "old"),
                entry("run", "same"),
                entry("image", "binary-old"),
            ],
            "delivered-second": [
                entry("added.txt", "new"),
                entry("run", "same", "100755"),
                entry("image", "binary-new"),
            ],
        }[revision]

    async def blobs(oids):
        contents = {
            "old": b"old\n",
            "new": b"new\n",
            "same": b"run\n",
            "binary-old": b"a\x00",
            "binary-new": b"b\x00",
        }
        return {oid: contents[oid] for oid in oids}

    monkeypatch.setattr(reader, "committed_entries", entries)
    monkeypatch.setattr(reader, "committed_blobs", blobs)
    changes = {
        item["path"]: item
        for item in await reader.compare_revisions(
            "delivered-first", "delivered-second"
        )
    }
    assert changes["gone.txt"]["status"] == "removed"
    assert "-old" in changes["gone.txt"]["diff"]
    assert changes["added.txt"]["status"] == "added"
    assert "+new" in changes["added.txt"]["diff"]
    assert changes["image"]["diff"] is None
    assert changes["image"]["identical"] is False
    assert changes["run"]["before_mode"] == "100644"
    assert changes["run"]["after_mode"] == "100755"
    assert changes["run"]["diff"] == ""
