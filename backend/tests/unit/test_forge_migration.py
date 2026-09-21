"""Legacy refs and uncommitted files remain recoverable through migration."""

import tarfile
import uuid

import pytest

from app.domain.project.forge_migration import (
    freeze,
    git,
    push,
    references,
    task_snapshot,
    unpack_working_files,
)


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


def test_task_bundle_restores_archived_files_after_source_changes(tmp_path):
    root = tmp_path / "workspaces"
    project = uuid.uuid4()
    source, worktree = legacy_repository(root, project)
    (worktree / "file.txt").write_text("unfinished task\n")
    (worktree / "link").symlink_to("file.txt")
    backup = tmp_path / "backup"
    frozen = freeze(root, project, backup)
    # Resuming uses the archived input even if the old live directory changed.
    (worktree / "file.txt").write_text("later edits\n")
    task = uuid.uuid4()
    working_files = tmp_path / "working-files"
    unpack_working_files(backup, working_files)
    saved = task_snapshot(
        backup, task, working_files=working_files, directory="task", branch="task"
    )
    assert (
        task_snapshot(
            backup, task, working_files=working_files, directory="task", branch="task"
        )
        == saved
    )
    restored = tmp_path / "restored"
    git(tmp_path, "clone", str(backup / "repository.git"), str(restored))
    git(restored, "fetch", str(backup / saved["file"]), f"refs/cheese/snapshots/{task}")
    git(restored, "checkout", "--detach", saved["snapshot_sha"])
    assert (restored / "file.txt").read_text() == "unfinished task\n"
    assert (restored / "new.txt").read_bytes() == b"untracked\x00bytes"
    assert (restored / "link").is_symlink()
    assert git(source, "show", ":file.txt") == "staged\n"
    assert references(backup / "repository.git") == frozen["refs"]
    assert freeze(root, project, backup) == frozen


@pytest.mark.parametrize("dirty", [False, True])
def test_github_backup_recovers_unpublished_history_in_an_empty_repository(
    tmp_path, dirty
):
    root = tmp_path / "workspaces"
    project = uuid.uuid4()
    _, worktree = legacy_repository(root, project)
    (worktree / "new.txt").unlink()
    (worktree / "file.txt").write_text("unpublished commit\n")
    git(worktree, "commit", "-am", "Work absent from GitHub")
    head = git(worktree, "rev-parse", "HEAD").strip()
    if dirty:
        (worktree / "new.bin").write_bytes(b"\x00unfinished\xff")
    backup = tmp_path / "backup"
    freeze(root, project, backup)
    task = uuid.uuid4()
    working_files = tmp_path / "working-files"
    unpack_working_files(backup, working_files)
    saved = task_snapshot(
        backup,
        task,
        working_files=working_files,
        directory="task",
        branch="task",
        include_history=True,
    )
    restored = tmp_path / "restored"
    restored.mkdir()
    git(restored, "init")
    git(restored, "bundle", "verify", str(backup / saved["file"]))
    git(restored, "fetch", str(backup / saved["file"]), f"refs/cheese/snapshots/{task}")
    git(restored, "checkout", "--detach", saved["snapshot_sha"])
    assert (restored / "file.txt").read_text() == "unpublished commit\n"
    assert git(restored, "show", f"{head}:file.txt") == "unpublished commit\n"
    if dirty:
        assert (restored / "new.bin").read_bytes() == b"\x00unfinished\xff"
    else:
        assert saved["snapshot_sha"] == head


def test_multiple_tasks_recover_from_one_expanded_checkpoint(tmp_path):
    root = tmp_path / "workspaces"
    project = uuid.uuid4()
    source, first = legacy_repository(root, project)
    second = first.parent / "second"
    git(source, "worktree", "add", "-b", "second", str(second))
    (first / "file.txt").write_text("first unfinished version\n")
    (second / "file.txt").write_text("second unfinished version\n")
    backup = tmp_path / "backup"
    freeze(root, project, backup)
    working_files = tmp_path / "working-files"
    unpack_working_files(backup, working_files)
    archive = backup / "working-files.tar.gz"
    archive.rename(backup / "retained-working-files.tar.gz")
    restored = tmp_path / "restored"
    git(tmp_path, "clone", str(backup / "repository.git"), str(restored))
    for directory, expected in (
        ("task", "first unfinished version\n"),
        ("second", "second unfinished version\n"),
    ):
        task = uuid.uuid4()
        saved = task_snapshot(
            backup,
            task,
            working_files=working_files,
            directory=directory,
            branch=directory,
        )
        git(
            restored,
            "fetch",
            str(backup / saved["file"]),
            f"refs/cheese/snapshots/{task}",
        )
        git(restored, "checkout", "--detach", saved["snapshot_sha"])
        assert (restored / "file.txt").read_text() == expected
        assert (
            working_files / ".worktrees" / str(project) / directory / "file.txt"
        ).read_text() == expected
