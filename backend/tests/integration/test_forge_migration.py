"""Existing GitHub projects archive local work without changing their remote refs."""

import json
import tarfile
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.domain.project.forge_migration import git, references
from app.domain.project.models import Project, ProjectForge
from app.domain.room_task.models import Task
from app.domain.topic.models import Topic
from scripts import migrate_forge
from tests.integration.conftest import a_team
from tests.unit.test_forge_migration import legacy_repository


@pytest.mark.anyio
async def test_github_local_work_survives_migration_without_remote_writes(
    db_factory, monkeypatch, tmp_path
):
    root = tmp_path / "workspaces"
    monkeypatch.setattr(settings, "workspace_root", str(root))
    monkeypatch.setattr(migrate_forge, "async_session_factory", db_factory)
    tokens = AsyncMock(side_effect=AssertionError("GitHub must remain unchanged"))
    monkeypatch.setattr(migrate_forge, "tokens_for_project", tokens)
    async with db_factory() as session:
        project = Project(team_id=await a_team(session), name="Existing GitHub project")
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
        second = Task(
            project_id=project.id,
            room_id=room.id,
            title="Another unpublished task",
            branch_name="second",
            workspace_name="second",
            base_branch="main",
        )
        session.add(second)
        await session.commit()
    source, worktree = legacy_repository(root, project.id)
    git(source, "worktree", "add", "-b", "second", str(worktree.parent / "second"))
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
    # The working files of every task are in the one immutable archive: the
    # platform uploads neither the commit GitHub has not seen nor the file
    # nobody committed.
    (archive,) = sorted((backups / str(project.id)).glob("*/working-files.tar.gz"))
    with tarfile.open(archive) as bundle:
        names = bundle.getnames()
        untracked = next(n for n in names if n.endswith("/task/new.txt"))
        assert bundle.extractfile(untracked).read() == b"untracked\x00bytes"
        second_file = next(n for n in names if n.endswith("/second/file.txt"))
        assert bundle.extractfile(second_file).read() == b"committed\n"


@pytest.mark.anyio
async def test_a_project_created_on_forgejo_leaves_nothing_to_migrate(
    db_factory, monkeypatch, tmp_path, capsys
):
    """The release preflight finds no pending work, so writers keep running."""
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "workspaces"))
    monkeypatch.setattr(migrate_forge, "async_session_factory", db_factory)
    async with db_factory() as session:
        project = Project(team_id=await a_team(session), name="Created on Forgejo")
        session.add(project)
        await session.flush()
        session.add(
            ProjectForge(
                project_id=project.id,
                kind="forgejo",
                repo=f"cheese-{project.id.hex}/project",
                url=f"https://forge.example/cheese-{project.id.hex}/project.git",
                api_url="http://forgejo.invalid/api/v1",
                default_branch="main",
                account_password="sealed",
            )
        )
        await session.commit()
    monkeypatch.setattr(
        "sys.argv",
        [
            "migrate_forge",
            "--backup-root",
            str(tmp_path / "backups"),
            "--check",
        ],
    )
    await migrate_forge.main()  # Exit 2 would make the deploy stop the backend.
    counts = json.loads(capsys.readouterr().out)
    assert counts == {"migrated": 0, "skipped": 1, "planned": 0, "failed": 0}
