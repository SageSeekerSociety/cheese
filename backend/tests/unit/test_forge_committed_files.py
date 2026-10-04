"""Committed file lists must survive the forge's comparison response limits."""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import GatewayUnavailableError
from app.domain.repository import forge_files


@pytest.mark.anyio
async def test_empty_forge_tree_lists_no_committed_files(monkeypatch):
    files = forge_files.ProjectFiles(None, uuid.uuid4(), None)
    monkeypatch.setattr(
        files, "_data", AsyncMock(return_value={"tree": None, "truncated": False})
    )
    assert await files.committed_entries("empty-commit") == []


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


class _GitHubTrees:
    """GitHub's git-trees endpoint over a nested repository, counting requests.

    Without `recursive` a tree lists its own children; with it, every entry
    below it by full path, in one response.
    """

    def __init__(self, depth):
        self.requests = 0
        self.trees = {}
        children = []
        for level in reversed(range(depth)):
            sha = f"tree-{level}"
            self.trees[sha] = [
                {
                    "path": f"file-{level}.txt",
                    "type": "blob",
                    "mode": "100644",
                    "sha": f"blob-{level}",
                    "size": level,
                },
                *children,
            ]
            children = [
                {"path": f"dir-{level}", "type": "tree", "mode": "040000", "sha": sha}
            ]
        self.root = "tree-0"

    def _below(self, sha, prefix=""):
        for entry in self.trees[sha]:
            yield {**entry, "path": prefix + entry["path"]}
            if entry["type"] == "tree":
                yield from self._below(entry["sha"], prefix + entry["path"] + "/")

    async def __call__(self, _project_id, _session, path, **_kwargs):
        self.requests += 1
        sha = path.removeprefix("/git/trees/").split("?", 1)[0]
        tree = list(self._below(sha)) if "recursive=" in path else self.trees[sha]
        return {"tree": tree, "truncated": False}


@pytest.mark.anyio
async def test_listing_a_deep_repository_is_not_one_request_per_directory(
    monkeypatch,
):
    forge = _GitHubTrees(depth=40)
    files = forge_files.ProjectFiles(None, uuid.uuid4(), None)
    monkeypatch.setattr(files, "_data", forge)

    entries = await files.committed_entries(forge.root)

    deepest = "/".join(f"dir-{n}" for n in range(1, 40)) + "/file-39.txt"
    assert len(entries) == 40
    assert deepest in {e["path"] for e in entries}
    assert "file-0.txt" in {e["path"] for e in entries}
    assert all(e["kind"] == "blob" for e in entries)
    assert forge.requests == 1


@pytest.mark.anyio
async def test_a_forge_that_never_answers_fails_the_listing_in_bounded_time(
    monkeypatch,
):
    async def silent(*_args, **_kwargs):
        await asyncio.sleep(3600)

    files = forge_files.ProjectFiles(None, uuid.uuid4(), None)
    monkeypatch.setattr(files, "_data", silent)
    monkeypatch.setattr(forge_files, "TREE_DEADLINE_S", 0.2, raising=False)

    async with asyncio.timeout(5):
        with pytest.raises(GatewayUnavailableError):
            await files.committed_entries("head")
