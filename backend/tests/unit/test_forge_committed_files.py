"""Committed file lists must survive the forge's comparison response limits."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.repository import forge_files


@pytest.mark.anyio
async def test_github_comparison_includes_files_beyond_its_300_file_cap(monkeypatch):
    task = SimpleNamespace(
        id=uuid.uuid4(),
        branch_name="task/report",
        base_branch="main",
        delivered_head=None,
    )
    files = forge_files.ProjectFiles(None, uuid.uuid4(), task.id)
    monkeypatch.setattr(files, "task", AsyncMock(return_value=task))
    monkeypatch.setattr(forge_files, "branch_head", AsyncMock(return_value="head"))
    monkeypatch.setattr(
        forge_files,
        "binding_for_project",
        AsyncMock(return_value=SimpleNamespace(kind="github_app")),
    )
    monkeypatch.setattr(
        forge_files,
        "repository_data",
        AsyncMock(
            return_value={
                "merge_base_commit": {"sha": "ancestor"},
                "files": [{"filename": f"file-{n}"} for n in range(300)],
            }
        ),
    )

    def entry(path, oid="same", mode="100644"):
        return {"path": path, "oid": oid, "mode": mode}

    async def tree(revision):
        if revision == "ancestor":
            return [entry("deleted"), entry("unchanged"), entry("executable")]
        assert revision == "head"
        return [entry(f"file-{n}") for n in range(301)] + [
            entry("unchanged"),
            entry("executable", mode="100755"),
        ]

    monkeypatch.setattr(files, "committed_entries", tree)
    changed = (await files.comparison())["files"]
    assert len(changed) == 303
    assert {"filename": "file-300", "status": "added"} in changed
    assert {"filename": "deleted", "status": "removed"} in changed
    assert {"filename": "executable", "status": "modified"} in changed
    assert "unchanged" not in {row["filename"] for row in changed}
