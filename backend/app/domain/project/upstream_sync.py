"""Fast-forward an opted-in Forgejo repository from its public GitHub upstream."""

import asyncio
import base64
import fcntl
import hashlib
import json
import logging
import os
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select

from app.core.db import SessionFactory
from app.domain.agent.forgejo_tokens import ForgejoTokens
from app.domain.project.models import Project, ProjectForge

logger = logging.getLogger(__name__)
_SETTING = "forge_upstream_url"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _event(path: Path, **data: object) -> None:
    line = json.dumps({"at": _now(), **data}, sort_keys=True) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(line)
        stream.flush()
        os.fsync(stream.fileno())


def _git(
    path: Path, *args: str, auth: str | None = None
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    if auth is not None:
        env.update(
            GIT_CONFIG_COUNT="1",
            GIT_CONFIG_KEY_0="http.extraHeader",
            GIT_CONFIG_VALUE_0="Authorization: Basic "
            + base64.b64encode(f"token:{auth}".encode()).decode(),
        )
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode and not (args[0] == "merge-base" and result.returncode == 1):
        error = result.stderr.replace(auth, "[redacted]") if auth else result.stderr
        raise RuntimeError(f"git {args[0]} failed: {error.strip()}")
    return result


def _github_url(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("upstream must be a public GitHub repository URL")
    parsed = urlsplit(value)
    parts = parsed.path.strip("/").removesuffix(".git").split("/")
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or len(parts) != 2
        or not all(parts)
    ):
        raise ValueError("upstream must be a public GitHub repository URL")
    return f"https://github.com/{parts[0]}/{parts[1]}.git"


def sync_one(
    directory: Path,
    *,
    project_id: uuid.UUID,
    upstream: str,
    destination: str,
    branch: str,
    token: str,
) -> dict[str, str]:
    """Use the destination Git ref as the checkpoint; never force a remote ref."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "skipped", "reason": "another sync is running"}

        cache = directory / "repository.git"
        if not cache.exists():
            cache.mkdir(mode=0o700)
        if not (cache / "HEAD").exists():
            _git(cache, "init", "--bare", "--quiet")
        symrefs = _git(cache, "ls-remote", "--symref", upstream, "HEAD").stdout
        source_ref = next(
            (
                line.split("\t", 1)[0].removeprefix("ref: ")
                for line in symrefs.splitlines()
                if line.startswith("ref: ") and line.endswith("\tHEAD")
            ),
            None,
        )
        if source_ref is None or not source_ref.startswith("refs/heads/"):
            raise RuntimeError("upstream default branch is unavailable")
        _git(
            cache,
            "fetch",
            "--no-tags",
            upstream,
            f"+{source_ref}:refs/remotes/upstream/default",
        )
        source_sha = _git(
            cache, "rev-parse", "refs/remotes/upstream/default"
        ).stdout.strip()
        target_ref = f"refs/heads/{branch}"
        _git(
            cache,
            "fetch",
            "--no-tags",
            destination,
            f"+{target_ref}:refs/remotes/forge/default",
            auth=token,
        )
        target_sha = _git(
            cache, "rev-parse", "refs/remotes/forge/default"
        ).stdout.strip()
        if source_sha == target_sha:
            return {"status": "equal", "sha": source_sha}
        if _git(
            cache, "merge-base", "--is-ancestor", target_sha, source_sha
        ).returncode:
            raise RuntimeError(
                f"destination is not an ancestor: {target_sha} != {source_sha}"
            )
        _git(cache, "push", destination, f"{source_sha}:{target_ref}", auth=token)
        observed = _git(cache, "ls-remote", destination, target_ref, auth=token).stdout
        if not observed.startswith(source_sha + "\t"):
            raise RuntimeError("destination head did not match the pushed commit")

        checkpoint = {
            "at": _now(),
            "project_id": str(project_id),
            "upstream": upstream,
            "destination": destination,
            "branch": branch,
            "previous_sha": target_sha,
            "sha": source_sha,
        }
        checkpoint_dir = directory / "checkpoints"
        checkpoint_dir.mkdir(mode=0o700, exist_ok=True)
        checkpoint_path = (
            checkpoint_dir / f"{checkpoint['at'].replace(':', '-')}-{source_sha}.json"
        )
        fd = os.open(checkpoint_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(checkpoint, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        return {"status": "updated", "previous_sha": target_sha, "sha": source_sha}


async def sweep(sessions: SessionFactory, root: Path | None = None) -> dict[str, int]:
    """Sync configured projects; the persistent event log covers every attempt."""
    root = root or Path.home() / "forge-upstream-sync"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    async with sessions() as session:
        projects = (
            await session.execute(
                select(Project.id, Project.settings, ProjectForge)
                .join(ProjectForge, ProjectForge.project_id == Project.id)
                .where(ProjectForge.kind == "forgejo")
            )
        ).all()
    counts = {"updated": 0, "equal": 0, "skipped": 0, "failed": 0}
    for project_id, project_settings, binding in projects:
        upstream = (project_settings or {}).get(_SETTING)
        if not upstream:
            continue
        directory = root / str(project_id)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        events = directory / "events.jsonl"
        run_id = uuid.uuid4().hex
        try:
            source = _github_url(upstream)
        except ValueError:
            source = None
        await asyncio.to_thread(
            _event,
            events,
            project_id=str(project_id),
            run_id=run_id,
            status="start",
            upstream=source
            or "invalid:sha256=" + hashlib.sha256(repr(upstream).encode()).hexdigest(),
            destination=binding.url,
            branch=binding.default_branch,
        )
        try:
            if source is None:
                raise ValueError("upstream must be a public GitHub repository URL")
            token, _ = await ForgejoTokens(
                binding, sessions=sessions
            ).installation_token()
            result = await asyncio.to_thread(
                sync_one,
                directory,
                project_id=project_id,
                upstream=source,
                destination=binding.url,
                branch=binding.default_branch,
                token=token,
            )
            counts[result["status"]] += 1
        except Exception as exc:  # noqa: BLE001 — next cycle retries this project
            counts["failed"] += 1
            result = {"status": "failed", "reason": str(exc)}
            logger.exception("forge upstream sync failed for project %s", project_id)
        await asyncio.to_thread(
            _event, events, project_id=str(project_id), run_id=run_id, **result
        )
    return counts
