"""An existing Forgejo repository only follows an upstream by fast-forward."""

import json
import subprocess
import uuid
from types import SimpleNamespace
from typing import cast

import pytest

from app.core.db import SessionFactory
from app.domain.project.upstream_sync import _github_url, sweep, sync_one


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_existing_repository_syncs_without_replacing_divergent_work(tmp_path):
    upstream = tmp_path / "upstream.git"
    destination = tmp_path / "destination.git"
    work = tmp_path / "work"
    for bare in (upstream, destination):
        bare.mkdir()
        git(bare, "init", "--bare", "--initial-branch=main")
    git(tmp_path, "clone", str(upstream), str(work))
    git(work, "config", "user.name", "Test")
    git(work, "config", "user.email", "test@example.invalid")
    (work / "file.txt").write_text("first\n")
    git(work, "add", "file.txt")
    git(work, "commit", "-m", "first")
    git(work, "push", "origin", "HEAD:main")
    git(work, "push", str(destination), "HEAD:main")

    (work / "file.txt").write_text("second\n")
    git(work, "commit", "-am", "second")
    git(work, "push", "origin", "HEAD:main")
    source_sha = git(work, "rev-parse", "HEAD")
    directory = tmp_path / "sync"
    kwargs = {
        "project_id": uuid.uuid4(),
        "upstream": str(upstream),
        "destination": str(destination),
        "branch": "main",
        "token": "test-token",
    }
    result = sync_one(directory, **kwargs)
    assert result["status"] == "updated"
    assert git(destination, "rev-parse", "main") == source_sha
    assert len(list((directory / "checkpoints").glob("*.json"))) == 1
    assert sync_one(directory, **kwargs) == {"status": "equal", "sha": source_sha}

    # A destination-only commit is preserved; the next upstream commit cannot
    # overwrite it, even though the two repositories still share history.
    git(work, "remote", "add", "forge", str(destination))
    git(work, "checkout", "-b", "destination-only")
    (work / "file.txt").write_text("destination\n")
    git(work, "commit", "-am", "destination")
    git(work, "push", "forge", "HEAD:main")
    destination_sha = git(destination, "rev-parse", "main")
    git(work, "checkout", "main")
    (work / "file.txt").write_text("third\n")
    git(work, "commit", "-am", "third")
    git(work, "push", "origin", "HEAD:main")

    with pytest.raises(RuntimeError, match="not an ancestor"):
        sync_one(directory, **kwargs)
    assert git(destination, "rev-parse", "main") == destination_sha


@pytest.mark.parametrize(
    "value",
    [
        "file:///tmp/repo",
        "http://github.com/org/repo",
        "https://github.com/org/repo?x=1",
    ],
)
def test_upstream_accepts_only_public_github_https(value):
    with pytest.raises(ValueError, match="public GitHub"):
        _github_url(value)


def test_upstream_canonical_url():
    assert _github_url("https://github.com/org/repo") == (
        "https://github.com/org/repo.git"
    )


@pytest.mark.anyio
async def test_sweep_logs_start_and_failure_without_exposing_invalid_url(tmp_path):
    project_id = uuid.uuid4()

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, _statement):
            return SimpleNamespace(
                all=lambda: [
                    (
                        project_id,
                        {"forge_upstream_url": "https://secret@github.com/org/repo"},
                        SimpleNamespace(
                            url="https://forge.example/repo.git", default_branch="main"
                        ),
                    )
                ]
            )

    root = tmp_path / "sync"
    assert await sweep(cast(SessionFactory, Session), root) == {
        "updated": 0,
        "equal": 0,
        "skipped": 0,
        "failed": 1,
    }
    events = (root / str(project_id) / "events.jsonl").read_text().splitlines()
    assert [json.loads(line)["status"] for line in events] == ["start", "failed"]
    assert json.loads(events[0])["run_id"] == json.loads(events[1])["run_id"]
    assert "secret" not in "\n".join(events)
