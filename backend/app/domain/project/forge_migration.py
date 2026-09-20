"""Freeze legacy repositories before copying their references to the forge."""

import base64
import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
import uuid
from pathlib import Path


def git(path: Path, *args: str, env: dict | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout


def references(repository: Path) -> dict[str, str]:
    return dict(
        line.split(" ", 1)
        for line in git(
            repository, "for-each-ref", "--format=%(refname) %(objectname)"
        ).splitlines()
    )


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_checkpoint(path: Path, value: dict) -> None:
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(json.dumps(value, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def freeze(root: Path, project_id: uuid.UUID, output: Path) -> dict:
    """Checkpoint all refs and working files; the operator must stop writers first."""
    root = root.resolve()
    output = output.resolve()
    repository = root / str(project_id)
    if repository.is_symlink():
        raise ValueError("Repository symlink must be resolved before migration")
    if output.is_relative_to(repository):
        raise ValueError("Migration backup must be outside the source repository")
    checkpoint = output / "source.json"
    if checkpoint.exists():
        saved = json.loads(checkpoint.read_text())
        if saved["project_id"] != str(project_id) or saved["workspace_root"] != str(
            root
        ):
            raise ValueError("Checkpoint belongs to a different migration source")
        if digest(output / "working-files.tar.gz") != saved["archive_sha256"]:
            raise ValueError("Migration backup checksum mismatch")
        if references(output / "repository.git") != saved["refs"]:
            raise ValueError("Migration reference checkpoint mismatch")
        return saved
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any(output.iterdir()):
        # An interrupted preparation has no verified checkpoint. Keep its files
        # for inspection and require a new output directory for that preparation.
        raise ValueError("Incomplete backup exists; choose a fresh backup directory")
    refs = references(repository)
    sources = [repository]
    tasks = root / ".worktrees" / str(project_id)
    if tasks.is_symlink():
        raise ValueError("Task directory symlink must be resolved before migration")
    if tasks.exists():
        if output.is_relative_to(tasks):
            raise ValueError("Migration backup must be outside task worktrees")
        sources.append(tasks)
    for line in git(repository, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            worktree = Path(line.removeprefix("worktree ")).resolve()
            if not any(worktree.is_relative_to(source) for source in sources):
                raise ValueError(
                    f"Worktree outside the project backup scope: {worktree}"
                )
    archive = output / "working-files.tar.gz"
    with tarfile.open(archive, "w:gz", dereference=False) as bundle:
        for source in sources:
            bundle.add(source, arcname=str(source.relative_to(root)))
    git(
        root,
        "clone",
        "--mirror",
        "--no-hardlinks",
        "--dissociate",
        str(repository),
        str(output / "repository.git"),
    )
    if references(repository) != refs or references(output / "repository.git") != refs:
        raise RuntimeError("Source references changed while preparing the migration")
    saved = {
        "project_id": str(project_id),
        "workspace_root": str(root),
        "git_version": git(root, "--version").strip(),
        "refs": refs,
        "default_branch": git(repository, "symbolic-ref", "--short", "HEAD").strip(),
        "archive_sha256": digest(archive),
        "archived_paths": [str(source.relative_to(root)) for source in sources],
    }
    write_checkpoint(checkpoint, saved)
    return saved


def push(output: Path, url: str, token: str) -> dict[str, str]:
    """Resume an equal or empty destination; never replace a different remote ref."""
    repository = output / "repository.git"
    expected = references(repository)
    env = os.environ.copy()
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.extraHeader",
            "GIT_CONFIG_VALUE_0": "Authorization: Basic "
            + base64.b64encode(f"token:{token}".encode()).decode(),
        }
    )

    def remote_refs():
        return {
            ref: sha
            for sha, ref in (
                line.split("\t", 1)
                for line in git(
                    repository, "ls-remote", "--refs", url, env=env
                ).splitlines()
            )
        }

    before = remote_refs()
    if any(expected.get(ref) != sha for ref, sha in before.items()):
        raise ValueError(
            "Destination has different references; nothing was overwritten"
        )
    if expected:
        git(repository, "push", "--atomic", url, "refs/*:refs/*", env=env)
    after = remote_refs()
    if after != expected:
        raise RuntimeError(
            "Destination references do not match the migration checkpoint"
        )
    return after


def task_snapshot(
    output: Path, task_id: uuid.UUID, *, directory: str, branch: str
) -> dict | None:
    """Build a recoverable task bundle from the immutable archive."""
    source = json.loads((output / "source.json").read_text())
    prefix = f".worktrees/{source['project_id']}/{directory}"
    if Path(directory).is_absolute() or ".." in Path(directory).parts:
        raise ValueError("Task directory escapes its project's worktrees")
    saved_path = output / f"task-{task_id}.json"
    if saved_path.exists():
        saved = json.loads(saved_path.read_text())
        if saved["directory"] != directory or saved["branch"] != branch:
            raise ValueError("Task workspace changed after the migration checkpoint")
        if digest(output / saved["file"]) != saved["digest"]:
            raise ValueError("Task migration bundle checksum mismatch")
        return saved
    head = source["refs"].get(f"refs/heads/{branch}")
    with tempfile.TemporaryDirectory(dir=output, prefix="snapshot-") as temporary:
        temporary = Path(temporary)
        with tarfile.open(output / "working-files.tar.gz") as archive:
            members = [
                entry
                for entry in archive.getmembers()
                if entry.name == prefix or entry.name.startswith(prefix + "/")
            ]
            if not members:
                return None
            if head is None:
                raise ValueError(f"Task branch {branch} is missing from the checkpoint")
            # The archive was created locally. tar_filter confines writes to
            # this directory while preserving worktree symlinks as symlinks.
            archive.extractall(temporary, members=members, filter="tar")
        worktree = temporary / prefix
        repository = temporary / "builder.git"
        git(temporary, "init", "--bare", str(repository))
        (repository / "objects" / "info" / "alternates").write_text(
            str((output / "repository.git" / "objects").resolve()) + "\n"
        )
        env = os.environ.copy()
        # A retry produces the same backup commit and object key.
        date = git(
            output / "repository.git", "show", "-s", "--format=%cI", head
        ).strip()
        env.update(
            {
                "GIT_DIR": str(repository),
                "GIT_WORK_TREE": str(worktree),
                "GIT_INDEX_FILE": str(repository / "index"),
                "GIT_AUTHOR_NAME": "Cheese migration",
                "GIT_COMMITTER_NAME": "Cheese migration",
                "GIT_AUTHOR_EMAIL": "migration@users.invalid",
                "GIT_COMMITTER_EMAIL": "migration@users.invalid",
                "GIT_AUTHOR_DATE": date,
                "GIT_COMMITTER_DATE": date,
            }
        )
        git(repository, "read-tree", head, env=env)
        git(repository, "add", "-A", env=env)
        tree = git(repository, "write-tree", env=env).strip()
        if tree == git(repository, "rev-parse", head + "^{tree}", env=env).strip():
            return None
        snapshot = git(
            repository,
            "commit-tree",
            tree,
            "-p",
            head,
            "-m",
            f"Uncommitted work for task {task_id}",
            env=env,
        ).strip()
        ref = f"refs/cheese/snapshots/{task_id}"
        git(repository, "update-ref", ref, snapshot, env=env)
        bundle = output / f"task-{task_id}-{snapshot}.bundle"
        if not bundle.exists():
            pending_bundle = temporary / "snapshot.bundle"
            git(
                repository,
                "bundle",
                "create",
                str(pending_bundle),
                ref,
                f"^{head}",
                env=env,
            )
            git(repository, "bundle", "verify", str(pending_bundle), env=env)
            pending_bundle.replace(bundle)
        git(repository, "bundle", "verify", str(bundle), env=env)
        if (
            git(repository, "bundle", "list-heads", str(bundle), env=env).strip()
            != f"{snapshot} {ref}"
        ):
            raise ValueError("Existing task bundle has different contents")
        saved = {
            "directory": directory,
            "branch": branch,
            "head_sha": head,
            "snapshot_sha": snapshot,
            "digest": digest(bundle),
            "file": bundle.name,
        }
        write_checkpoint(saved_path, saved)
        return saved
