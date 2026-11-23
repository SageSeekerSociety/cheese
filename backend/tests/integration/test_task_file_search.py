"""Searching the text of a task's files: the 编辑器 form's 搜索.

What is searched is what the 文件 tree lists — the task's working copy on its
machine, tracked files and untracked ones, with the repository's ignore rules
keeping the rest out — and only text: a binary file is never a match.
"""

import subprocess

import pytest

from app.domain.repository import forge_files
from tests.conftest import seed_user
from tests.delivery import delivery_task_id
from tests.integration.test_file_panel_safety import (
    BINARY,
    _mkproject,
    _mktopic,
    _owner,
    _put,
    _worktree,
    task_machine,  # noqa: F401
)

pytestmark = pytest.mark.usefixtures("task_machine")


def _search(client, pid, room, q, headers=None):
    return client.get(
        f"/projects/{pid}/files/search",
        params={
            "q": q,
            "topic": str(room),
            "task": str(delivery_task_id(client, room)),
        },
        headers=_owner(client) if headers is None else headers,
    )


def _commit(client, room, *paths):
    root = _worktree(client, room)
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "add", *paths], check=True, capture_output=True)
    subprocess.run(
        [*git, "-c", "commit.gpgsign=false", "commit", "-m", "start"],
        check=True,
        capture_output=True,
    )


def test_matches_in_tracked_and_untracked_files_come_back_by_file(client):
    pid = _mkproject(client)
    room = _mktopic(client, pid)
    _put(client, pid, room, "src/queue.c", b"int main() {\n  /* gcc -o queue */\n}\n")
    _commit(client, room, "src/queue.c")
    # Written since the last commit, not added: still the task's file.
    _put(client, pid, room, "README.md", "照着敲 GCC 那一行就能跑\n".encode())

    response = _search(client, pid, room, "gcc")

    assert response.status_code == 200, response.text
    body = response.json()["data"]
    found = {f["path"]: f["matches"] for f in body["files"]}
    assert set(found) == {"src/queue.c", "README.md"}
    [tracked] = found["src/queue.c"]
    assert tracked["line"] == 2
    assert tracked["text"] == "  /* gcc -o queue */"
    assert tracked["columns"] == [[5, 8]]
    # Case is ignored; the columns count characters as the browser does.
    [untracked] = found["README.md"]
    assert untracked["line"] == 1
    assert untracked["columns"] == [[4, 7]]
    assert body["truncated"] is False


def test_ignored_and_binary_files_are_not_searched(client):
    pid = _mkproject(client)
    room = _mktopic(client, pid)
    _put(client, pid, room, ".gitignore", b"build/\n")
    _put(client, pid, room, "build/out.txt", b"needle in the build output\n")
    _put(client, pid, room, "app.bin", BINARY + b"needle")
    _put(client, pid, room, "notes.txt", b"a needle here\n")

    response = _search(client, pid, room, "needle")

    assert response.status_code == 200, response.text
    assert [f["path"] for f in response.json()["data"]["files"]] == ["notes.txt"]


def test_a_search_with_more_matches_than_it_answers_says_it_was_cut(
    client, monkeypatch
):
    monkeypatch.setattr(forge_files, "SEARCH_MAX_MATCHES", 3)
    pid = _mkproject(client)
    room = _mktopic(client, pid)
    _put(client, pid, room, "many.txt", b"hit\n" * 10)

    response = _search(client, pid, room, "hit")

    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert sum(len(f["matches"]) for f in body["files"]) == 3
    assert body["truncated"] is True


def test_nothing_matching_is_an_empty_answer(client):
    pid = _mkproject(client)
    room = _mktopic(client, pid)
    _put(client, pid, room, "notes.txt", b"nothing to see\n")

    response = _search(client, pid, room, "absent")

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {"files": [], "truncated": False}


def test_someone_who_may_not_read_the_project_is_refused(client):
    pid = _mkproject(client)
    room = _mktopic(client, pid)
    _put(client, pid, room, "notes.txt", b"secret needle\n")
    outsider = {"Authorization": f"Bearer {seed_user(client, 'mallory')}"}

    assert client.get(f"/projects/{pid}/files", headers=outsider).status_code == 404
    response = _search(client, pid, room, "needle", headers=outsider)

    assert response.status_code == 404
    assert "secret" not in response.text
