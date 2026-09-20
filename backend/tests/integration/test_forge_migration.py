"""Existing GitHub projects keep local work without changing their remote refs."""

import shutil
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.core.storage import LocalStorageBackend
from app.domain.project.forge_migration import git, references
from app.domain.project.models import Project, ProjectForge
from app.domain.room_task import snapshots
from app.domain.room_task.models import Task
from app.domain.topic.models import Topic
from scripts import migrate_forge
from tests.unit.test_forge_migration import legacy_repository


@pytest.mark.anyio
async def test_github_local_work_survives_migration_without_remote_writes(
    db_factory, monkeypatch, tmp_path
):
    root = tmp_path / "workspaces"
    monkeypatch.setattr(settings, "workspace_root", str(root))
    monkeypatch.setattr(migrate_forge, "async_session_factory", db_factory)
    storage = LocalStorageBackend(str(tmp_path / "private"), "unused-private-url")
    monkeypatch.setattr(snapshots, "transcript_storage", lambda: storage)
    tokens = AsyncMock(side_effect=AssertionError("GitHub must remain unchanged"))
    monkeypatch.setattr(migrate_forge, "tokens_for_project", tokens)
    async with db_factory() as session:
        project = Project(name="Existing GitHub project")
        session.add(project)
        await session.flush()
        binding = ProjectForge(
            project_id=project.id,
            kind="github_app",
            repo="example/project",
            url="https://github.com/example/project.git",
            api_url="https://api.github.com",
            default_branch="main",
        )
        room = Topic(project_id=project.id, title="Unfinished work")
        session.add_all([binding, room])
        await session.flush()
        task = Task(
            project_id=project.id,
            room_id=room.id,
            title="Unpublished work",
            branch_name="task",
            workspace_name="task",
            base_branch="main",
        )
        session.add(task)
        await session.commit()
    source, worktree = legacy_repository(root, project.id)
    (worktree / "file.txt").write_text("unpublished history\n")
    git(worktree, "commit", "-am", "Local commit")
    before = references(source)
    backups = tmp_path / "backups"
    assert await migrate_forge.migrate(project.id, backups, apply=False) == "planned"
    assert await migrate_forge.migrate(project.id, backups, apply=True) == "migrated"
    assert await migrate_forge.migrate(project.id, backups, apply=True) == "skipped"
    assert references(source) == before
    tokens.assert_not_called()
    async with db_factory() as session:
        current = await migrate_forge.binding_for_project(project.id, session)
        assert current.kind == "github_app"
        assert current.repo == binding.repo
        assert current.default_branch == "main"
        snapshot = await snapshots.latest(session, task.id)
        bundle = tmp_path / "recovery.bundle"
        bundle.write_bytes(await snapshots.download(snapshot))
    # Recovery has no access to the old checkout or its migration archive.
    shutil.move(root, tmp_path / "retired-workspaces")
    shutil.move(backups, tmp_path / "retired-backups")
    restored = tmp_path / "restored"
    restored.mkdir()
    git(restored, "init")
    git(restored, "fetch", str(bundle), f"refs/cheese/snapshots/{task.id}")
    git(restored, "checkout", "--detach", snapshot.snapshot_sha)
    assert (restored / "file.txt").read_text() == "unpublished history\n"
    assert (restored / "new.txt").read_bytes() == b"untracked\x00bytes"
