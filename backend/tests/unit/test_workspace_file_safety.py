"""File versions identify content rather than write time."""

import base64
import hashlib
import uuid

import pytest

from app.domain.repository.forge_files import ProjectFiles
from app.domain.textfile import compare_bytes, content_version


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


class FakeForge:
    """A repository as the forge's tree and blob API serves it, counting reads.

    Ids are derived from content the way git's are, so an unchanged directory
    has the same id in both commits."""

    def __init__(self):
        self.objects: dict[str, object] = {}
        self.reads = 0

    def _put(self, value) -> str:
        oid = hashlib.sha1(repr(value).encode()).hexdigest()
        self.objects[oid] = value
        return oid

    def commit(self, files: dict[str, bytes | tuple[bytes, str]]) -> str:
        root: dict = {}
        for path, content in files.items():
            *dirs, name = path.split("/")
            node = root
            for part in dirs:
                node = node.setdefault(part, {})
            node[name] = content
        return self._tree(root)

    def _tree(self, node: dict) -> str:
        entries = []
        for name, value in sorted(node.items()):
            if isinstance(value, dict):
                entries.append(
                    {
                        "path": name,
                        "type": "tree",
                        "mode": "040000",
                        "sha": self._tree(value),
                    }
                )
                continue
            data, mode = value if isinstance(value, tuple) else (value, "100644")
            entries.append(
                {
                    "path": name,
                    "type": "blob",
                    "mode": mode,
                    "size": len(data),
                    "sha": self._put(data),
                }
            )
        return self._put(tuple(tuple(sorted(e.items())) for e in entries))

    async def data(self, path: str):
        self.reads += 1
        kind, oid = path.split("?")[0].split("/")[2:4]
        value = self.objects[oid]
        if kind == "blobs":
            return {"encoding": "base64", "content": base64.b64encode(value).decode()}
        return {"tree": [dict(entry) for entry in value], "truncated": False}


def _reader(monkeypatch, forge: FakeForge) -> ProjectFiles:
    reader = ProjectFiles(None, uuid.uuid4(), None)

    async def data(project_id, session, path, **kwargs):
        return await forge.data(path)

    monkeypatch.setattr(reader, "_data", data)
    return reader


@pytest.mark.anyio
async def test_two_delivered_trees_include_deletions_binary_and_mode_changes(
    monkeypatch,
):
    forge = FakeForge()
    first = forge.commit({"gone.txt": b"old\n", "bin/run": b"run\n", "image": b"a\x00"})
    second = forge.commit(
        {"added.txt": b"new\n", "bin/run": (b"run\n", "100755"), "image": b"b\x00"}
    )
    reader = _reader(monkeypatch, forge)
    changes = {
        item["path"]: item for item in await reader.compare_revisions(first, second)
    }
    assert set(changes) == {"gone.txt", "added.txt", "bin/run", "image"}
    assert changes["gone.txt"]["status"] == "removed"
    assert "-old" in changes["gone.txt"]["diff"]
    assert changes["added.txt"]["status"] == "added"
    assert "+new" in changes["added.txt"]["diff"]
    assert changes["image"]["diff"] is None
    assert changes["image"]["identical"] is False
    assert changes["bin/run"]["before_mode"] == "100644"
    assert changes["bin/run"]["after_mode"] == "100755"
    assert changes["bin/run"]["diff"] == ""


@pytest.mark.anyio
async def test_a_file_that_becomes_a_directory_is_compared_on_both_sides(monkeypatch):
    forge = FakeForge()
    first = forge.commit({"notes": b"flat\n"})
    second = forge.commit({"notes/today.md": b"nested\n"})
    reader = _reader(monkeypatch, forge)
    changes = {
        item["path"]: item for item in await reader.compare_revisions(first, second)
    }
    assert changes["notes"]["status"] == "removed"
    assert changes["notes/today.md"]["status"] == "added"


async def _cost_of_one_changed_file(monkeypatch, untouched_dirs: int) -> int:
    forge = FakeForge()
    rest = {f"lib/pkg{n}/mod.py": f"# {n}\n".encode() for n in range(untouched_dirs)}
    first = forge.commit({**rest, "app/main.py": b"print(1)\n"})
    second = forge.commit({**rest, "app/main.py": b"print(2)\n"})
    reader = _reader(monkeypatch, forge)
    changes = await reader.compare_revisions(first, second)
    assert [c["path"] for c in changes] == ["app/main.py"]
    return forge.reads


@pytest.mark.anyio
async def test_comparing_two_deliveries_costs_what_changed_not_the_repository(
    monkeypatch,
):
    assert await _cost_of_one_changed_file(
        monkeypatch, 300
    ) == await _cost_of_one_changed_file(monkeypatch, 3)


@pytest.mark.anyio
async def test_a_comparison_lists_every_changed_file_but_reads_a_bounded_number(
    monkeypatch,
):
    import app.domain.repository.forge_files as forge_files

    monkeypatch.setattr(forge_files, "MAX_DIFFED_FILES", 3)
    forge = FakeForge()
    first = forge.commit({f"f{n}.txt": b"a\n" for n in range(10)})
    second = forge.commit({f"f{n}.txt": b"b\n" for n in range(10)})
    reader = _reader(monkeypatch, forge)
    changes = await reader.compare_revisions(first, second)
    assert len(changes) == 10
    assert all(c["identical"] is False for c in changes)
    assert sum(c["diff"] is not None for c in changes) == 3
