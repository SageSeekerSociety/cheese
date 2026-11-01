"""Legacy refs and uncommitted files remain recoverable through migration."""

import tarfile
import uuid

import pytest

from app.domain.project.forge_migration import freeze, git, push, references


def unpack_working_files(backup, destination):
    """Expand the frozen project the way an operator recovers from it."""
    with tarfile.open(backup / "working-files.tar.gz") as archive:
        archive.extractall(destination, filter="tar")


def legacy_repository(root, project_id):
    source = root / str(project_id)
    source.mkdir(parents=True)
    git(source, "init", "-b", "main")
    git(source, "config", "user.email", "test@example.invalid")
    git(source, "config", "user.name", "Migration test")
    (source / "file.txt").write_text("committed\n")
    git(source, "add", ".")
    git(source, "commit", "-m", "Initial commit")
    git(source, "tag", "v1")
    git(source, "notes", "add", "-m", "Preserved note")
    worktree = root / ".worktrees" / str(project_id) / "task"
    worktree.parent.mkdir(parents=True)
    git(source, "worktree", "add", "-b", "task", str(worktree))
    (worktree / "new.txt").write_bytes(b"untracked\x00bytes")
    (source / "file.txt").write_text("staged\n")
    git(source, "add", "file.txt")
    (source / "file.txt").write_text("unstaged\n")
    return source, worktree


def test_migration_preserves_refs_index_and_working_files(tmp_path):
    root = tmp_path / "workspaces"
    project = uuid.uuid4()
    source, worktree = legacy_repository(root, project)
    expected = references(source)
    backup = tmp_path / "backup"
    saved = freeze(root, project, backup)
    assert saved["refs"] == expected
    assert freeze(root, project, backup) == saved
    with tarfile.open(backup / "working-files.tar.gz") as archive:
        assert archive.extractfile(f"{project}/file.txt").read() == b"unstaged\n"
        assert (
            archive.extractfile(f".worktrees/{project}/task/new.txt").read()
            == b"untracked\x00bytes"
        )
    assert git(source, "show", ":file.txt") == "staged\n"
    assert (worktree / "new.txt").exists()
    destination = tmp_path / "destination.git"
    git(tmp_path, "init", "--bare", str(destination))
    assert push(backup, str(destination), "unused") == expected
    assert push(backup, str(destination), "unused") == expected
    assert references(source) == expected


def test_migration_refuses_a_different_remote_without_overwriting(tmp_path):
    root = tmp_path / "workspaces"
    project = uuid.uuid4()
    source, _ = legacy_repository(root, project)
    backup = tmp_path / "backup"
    freeze(root, project, backup)
    destination = tmp_path / "destination.git"
    git(tmp_path, "init", "--bare", str(destination))
    git(source, "push", str(destination), "main:refs/heads/unrelated")
    before = references(destination)
    with pytest.raises(ValueError, match="different references"):
        push(backup, str(destination), "unused")
    assert references(destination) == before


def test_migration_archives_a_preserved_registered_worktree(tmp_path):
    root = tmp_path / "workspaces"
    project = uuid.uuid4()
    source, worktree = legacy_repository(root, project)
    preserved = root / ".preserved" / "cleanup" / str(project) / "task"
    preserved.parent.mkdir(parents=True)
    git(source, "worktree", "move", str(worktree), str(preserved))
    git(preserved, "checkout", "--detach")
    (preserved / "file.txt").write_text("preserved unfinished work\n")
    before = references(source)
    backup = tmp_path / "backup"
    saved = freeze(root, project, backup)
    relative = str(preserved.relative_to(root))
    assert relative in saved["archived_paths"]
    restored = tmp_path / "restored"
    unpack_working_files(backup, restored)
    assert (
        restored / relative / "file.txt"
    ).read_text() == "preserved unfinished work\n"
    assert (restored / relative / "new.txt").read_bytes() == b"untracked\x00bytes"
    assert references(source) == before
    assert freeze(root, project, backup) == saved


def test_migration_refuses_registered_worktree_outside_workspace_root(tmp_path):
    root = tmp_path / "workspaces"
    project = uuid.uuid4()
    source, worktree = legacy_repository(root, project)
    external = tmp_path / "external"
    git(source, "worktree", "move", str(worktree), str(external))
    with pytest.raises(ValueError, match="outside the workspace root"):
        freeze(root, project, tmp_path / "backup")
    assert (external / "new.txt").read_bytes() == b"untracked\x00bytes"


def test_corrupted_backup_cannot_be_resumed(tmp_path):
    root = tmp_path / "workspaces"
    project = uuid.uuid4()
    legacy_repository(root, project)
    backup = tmp_path / "backup"
    freeze(root, project, backup)
    with (backup / "working-files.tar.gz").open("ab") as stream:
        stream.write(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        freeze(root, project, backup)
