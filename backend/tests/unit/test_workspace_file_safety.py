"""File versions identify content rather than write time."""

import asyncio
import base64
import hashlib
import uuid
from types import SimpleNamespace

import pytest

from app.core.db import engine as app_engine
from app.domain.repository import forge_files
from app.domain.repository.forge_files import ProjectFiles
from app.domain.textfile import MAX_TEXT_BYTES, compare_bytes, content_version


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


def _project_session():
    """A stand-in for the session a request hands the reader.

    Nothing here reaches a database: the transport is faked below, and a session
    that runs no statement opens no connection. A read that goes out with others
    still mints a real session off this one's engine, which is the
    application's, so that part is real and not another fake."""
    return SimpleNamespace(bind=app_engine)


def _reader(monkeypatch, forge: FakeForge) -> ProjectFiles:
    """A reader over one repository, counting every request it makes.

    Both seams of the reader are faked, because a comparison uses both: the
    trees through `_data`, and the blobs through `_blob` — the last takes the
    session its read runs on, and the fake serves it from the same repository
    whatever session that is."""
    reader = ProjectFiles(_project_session(), uuid.uuid4(), None)

    async def data(project_id, session, path, **kwargs):
        return await forge.data(path)

    async def blob(entry, session=None):
        # Serving the repository does not depend on what the read runs on.
        payload = await forge.data(f"/git/blobs/{entry['sha']}")
        return base64.b64decode(payload["content"])

    monkeypatch.setattr(reader, "_data", data)
    monkeypatch.setattr(reader, "_blob", blob)
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
    monkeypatch.setattr(forge_files, "MAX_DIFFED_FILES", 3)
    forge = FakeForge()
    first = forge.commit({f"f{n}.txt": b"a\n" for n in range(10)})
    second = forge.commit({f"f{n}.txt": b"b\n" for n in range(10)})
    reader = _reader(monkeypatch, forge)
    changes = await reader.compare_revisions(first, second)
    assert len(changes) == 10
    assert all(c["identical"] is False for c in changes)
    assert sum(c["diff"] is not None for c in changes) == 3
    # The three read are the first three listed: the cap dates each file by its
    # place in the listing, which reads the same whether the blobs went out one
    # at a time or together.
    assert [c["path"] for c in changes if c["note"] is None] == [
        "f0.txt",
        "f1.txt",
        "f2.txt",
    ]
    assert [c["note"] for c in changes[3:]] == ["many"] * 7


def _changed_examples() -> tuple[dict, dict]:
    """Two commits whose comparison exercises every kind of change at once:
    an addition, text edits, a deletion, a mode-only change, binary content,
    one file over the text ceiling, and more edits than a low cap allows."""
    first = {
        "A-mode": b"same\n",
        "G-gone.txt": b"old\n",
        "big": b"x" * (MAX_TEXT_BYTES + 1),
        "bin0": b"a\x00",
        **{f"f{n}.txt": b"a\n" for n in range(10)},
    }
    second = {
        "0-added.txt": b"new\n",
        "A-mode": (b"same\n", "100755"),
        "big": b"y" * (MAX_TEXT_BYTES + 1),
        "bin0": b"b\x00",
        **{f"f{n}.txt": b"b\n" for n in range(10)},
    }
    return first, second


async def _read_one_file_at_a_time(reader, before, after, cap) -> list[tuple]:
    """What the comparison owes a caller, read as the rule says to read it.

    Walk the changed paths in order, read a file's two blobs only while fewer
    than `cap` files have been read, and say what that rule gives — the answer
    the reader owes whatever it does to get there."""
    old, new = await reader._changed_entries(before, after)
    said, read = [], 0
    for path in sorted(old.keys() | new.keys()):
        left, right = old.get(path), new.get(path)
        if left == right:
            continue
        entries = [entry for entry in (left, right) if entry]
        identical, diff, note = False, None, "unsupported"
        if read >= cap:
            note = "many"
        elif all(
            entry["kind"] == "blob" and entry["bytes"] <= MAX_TEXT_BYTES
            for entry in entries
        ):
            read += 1
            blobs = await reader.committed_blobs([entry["oid"] for entry in entries])
            result = compare_bytes(
                blobs[left["oid"]] if left else b"",
                blobs[right["oid"]] if right else b"",
                path if left else "/dev/null",
                path if right else "/dev/null",
            )
            identical, diff = result["identical"], result["diff"]
            # An answer with no diff to show reads as unsupported, whatever
            # kept it out: this comparison says which files it could not diff,
            # not why.
            note = "unsupported" if diff is None else result["note"]
        said.append(
            (
                path,
                "added" if left is None else "removed" if right is None else "modified",
                left["mode"] if left else None,
                right["mode"] if right else None,
                identical,
                diff,
                note,
            )
        )
    return said


def _as_tuples(changes: list[dict]) -> list[tuple]:
    return [
        (
            c["path"],
            c["status"],
            c["before_mode"],
            c["after_mode"],
            c["identical"],
            c["diff"],
            c["note"],
        )
        for c in changes
    ]


@pytest.mark.anyio
async def test_reading_files_together_says_what_reading_them_one_at_a_time_says(
    monkeypatch,
):
    monkeypatch.setattr(forge_files, "MAX_DIFFED_FILES", 4)
    first, second = _changed_examples()
    forge = FakeForge()
    one_at_a_time = await _read_one_file_at_a_time(
        _reader(monkeypatch, forge), forge.commit(first), forge.commit(second), 4
    )
    together = await _reader(monkeypatch, forge).compare_revisions(
        forge.commit(first), forge.commit(second)
    )
    assert _as_tuples(together) == one_at_a_time
    # And that answer is the one the rule asks for, not just one answer twice.
    assert [row[0] for row in one_at_a_time] == [
        "0-added.txt",
        "A-mode",
        "G-gone.txt",
        "big",
        "bin0",
        *[f"f{n}.txt" for n in range(10)],
    ]
    assert [row[1] for row in one_at_a_time] == [
        "added",
        "modified",
        "removed",
        *["modified"] * 12,
    ]
    assert [row[6] for row in one_at_a_time] == [
        None,  # 0-added.txt
        None,  # A-mode: a mode change, with both sides read for the line diff
        None,  # G-gone.txt
        "unsupported",  # big: over the text ceiling, so never read
        "unsupported",  # bin0: read, and the two sides are not text
        *["many"] * 10,  # f0.txt onwards: the cap of four is already spent
    ]
    assert one_at_a_time[1][4] is True  # A-mode: same bytes either side
    assert one_at_a_time[1][5] == ""  # and so no lines to show
    assert one_at_a_time[3][5] is None and one_at_a_time[4][5] is None
    assert "-old" in one_at_a_time[2][5] and "+new" in one_at_a_time[0][5]


async def _most_reads_at_once(monkeypatch, reader, forge, changes: int) -> int:
    """How many of the changed files' blobs were in the air together.

    Each read steps aside once and every read is counted while it is under way,
    so the number comes out the same on every machine. A reader that reads one
    file after another never gets above one."""
    first = forge.commit({f"f{n}.txt": f"{n}\n".encode() for n in range(changes)})
    second = forge.commit(
        {f"f{n}.txt": f"{n} changed\n".encode() for n in range(changes)}
    )
    under_way, most = 0, 0

    async def blob(entry, session=None):
        nonlocal under_way, most
        under_way += 1
        most = max(most, under_way)
        await asyncio.sleep(0)
        under_way -= 1
        payload = await forge.data(f"/git/blobs/{entry['sha']}")
        return base64.b64decode(payload["content"])

    monkeypatch.setattr(reader, "_blob", blob)
    compared = await reader.compare_revisions(first, second)
    assert [c["path"] for c in compared] == sorted(f"f{n}.txt" for n in range(changes))
    return most


@pytest.mark.anyio
async def test_a_comparison_reads_many_files_at_the_same_time(monkeypatch):
    monkeypatch.setattr(forge_files, "MAX_DIFFED_FILES", 20)
    forge = FakeForge()
    most = await _most_reads_at_once(
        monkeypatch, _reader(monkeypatch, forge), forge, 20
    )
    assert most > 1


@pytest.mark.anyio
async def test_a_comparison_reads_no_more_files_at_once_than_the_forge_can_take(
    monkeypatch,
):
    monkeypatch.setattr(forge_files, "MAX_DIFFED_FILES", 40)
    forge = FakeForge()
    most = await _most_reads_at_once(
        monkeypatch, _reader(monkeypatch, forge), forge, 40
    )
    # Forty files are two blobs each, well over the ceiling, so the ceiling is
    # what the comparison used: it is reached, and it holds.
    assert most == forge_files.MAX_BLOB_READS
    assert forge_files.MAX_BLOB_READS < 40


@pytest.mark.anyio
async def test_blobs_read_together_do_not_share_one_database_session(
    monkeypatch,
):
    """A session runs one statement at a time, and a read looks the project's
    binding and tokens up in the session it is given, so reads under way
    together each need their own — taken off the caller's engine, never the
    caller's own session, which the caller is still using."""
    monkeypatch.setattr(forge_files, "MAX_DIFFED_FILES", 6)
    forge = FakeForge()
    first = forge.commit({f"f{n}.txt": f"{n}\n".encode() for n in range(6)})
    second = forge.commit({f"f{n}.txt": f"{n} changed\n".encode() for n in range(6)})
    mine = _project_session()
    reader = ProjectFiles(mine, uuid.uuid4(), None)
    monkeypatched = _reader(monkeypatch, forge)
    monkeypatch.setattr(reader, "_data", monkeypatched._data)
    used, live = [], set()

    async def blob(entry, session):
        assert session is not mine
        assert session.bind is mine.bind
        assert session not in live, "two reads at once on one session"
        used.append(session)
        live.add(session)
        await asyncio.sleep(0)
        live.discard(session)
        payload = await forge.data(f"/git/blobs/{entry['sha']}")
        return base64.b64decode(payload["content"])

    monkeypatch.setattr(reader, "_blob", blob)
    changes = await reader.compare_revisions(first, second)
    assert len(changes) == 6
    assert len(used) == 12
    assert len({id(session) for session in used}) == 12


@pytest.mark.anyio
async def test_reading_blobs_answers_each_oid_however_often_it_was_asked_for(
    monkeypatch,
):
    """Files with the same content are one blob: asking for it once per file
    must not shift the answers onto the wrong oids."""
    reader = ProjectFiles(_project_session(), uuid.uuid4(), None)
    asked = []

    async def blob(entry, session):
        asked.append(entry["sha"])
        await asyncio.sleep(0)
        return entry["sha"].encode()

    monkeypatch.setattr(reader, "_blob", blob)
    assert await reader.committed_blobs(["a", "b", "a", "c", "b"]) == {
        "a": b"a",
        "b": b"b",
        "c": b"c",
    }
    assert sorted(asked) == ["a", "b", "c"]
    asked.clear()
    assert await reader.committed_blobs([]) == {}
    assert asked == []
