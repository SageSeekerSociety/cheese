"""Backups preserve dirty files without changing the task branch or its index."""

import hashlib
import importlib.util
import io
import json
import subprocess
import uuid
from importlib.machinery import SourceFileLoader
from pathlib import Path


def test_dirty_work_restores_separately_without_changing_staged_content(
    monkeypatch, tmp_path
):
    source = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"
    loader = SourceFileLoader("snapshot_cli", str(source))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    cli = importlib.util.module_from_spec(spec)
    loader.exec_module(cli)
    monkeypatch.setenv("HOME", str(tmp_path))
    task = str(uuid.uuid4())
    root = tmp_path / "tasks"
    work = root / task
    work.mkdir(parents=True)

    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(work), *args], text=True
        ).strip()

    git("init", "-b", "main")
    git("remote", "add", "origin", "https://old-platform.invalid/project/git")
    git("config", "user.name", "Test agent")
    git("config", "user.email", "agent@users.invalid")
    (work / "report.txt").write_text("base\n")
    git("add", ".")
    git("commit", "-m", "Base")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    git("switch", "-c", "task/report")
    (work / "report.txt").write_text("committed\n")
    git("commit", "-am", "Report")
    head = git("rev-parse", "HEAD")
    (work / "report.txt").write_text("staged\n")
    git("add", "report.txt")
    (work / "report.txt").write_text("unstaged\n")
    (work / "untracked.bin").write_bytes(b"\x00\xffbackup")
    metadata = dict(
        task_id=task,
        room_id="room",
        branch="task/report",
        base="main",
        closed=True,
        remote="https://forge.invalid/project/repo.git",
    )
    monkeypatch.setattr(cli, "PROJECT", "project")
    monkeypatch.setattr(cli, "TOPIC", "room")
    monkeypatch.setattr(cli, "_task_root", lambda: root)
    monkeypatch.setattr(cli, "_call", lambda *args: {"data": metadata})
    captured = {}

    def upload(request, **kwargs):
        captured["bundle"] = request.data.read()
        captured["snapshot_sha"] = request.full_url.rsplit("/", 1)[1]
        captured["digest"] = hashlib.sha256(captured["bundle"]).hexdigest()
        return io.BytesIO(json.dumps({"data": {"digest": captured["digest"]}}).encode())

    monkeypatch.setattr(cli.urllib.request, "urlopen", upload)
    cli._sync_task(task)
    assert git("rev-parse", "HEAD") == head
    assert git("show", ":report.txt") == "staged"
    assert (work / "report.txt").read_text() == "unstaged\n"
    monkeypatch.setattr(
        cli, "_call", lambda *args: {"data": {**captured, "id": "snapshot"}}
    )
    monkeypatch.setattr(cli, "_task_worktree", lambda *args, **kwargs: work)
    monkeypatch.setattr(
        cli,
        "_raw_request",
        lambda method, path, data, output: Path(output).write_bytes(captured["bundle"]),
    )
    cli._recover_task(task)
    recovered = next((tmp_path / ".cheese" / "recovered").iterdir())
    assert (recovered / "report.txt").read_text() == "unstaged\n"
    assert (recovered / "untracked.bin").read_bytes() == b"\x00\xffbackup"
    assert git("rev-parse", "HEAD") == head
    assert git("show", ":report.txt") == "staged"
