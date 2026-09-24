"""Caller-visible, offline project archives; never serialize credentials or memory."""

import base64
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import anyio
from anyio.to_thread import run_sync
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver
from app.auth.project_access import may_read_project
from app.core.config import settings
from app.core.errors import (
    AuthenticationRequiredError,
    ForbiddenError,
    GatewayUnavailableError,
)
from app.domain.block.models import Block, BlockKind
from app.domain.identity.services import IdentityService
from app.domain.library.service import artifact_snapshot_path
from app.domain.project import artifacts, forge
from app.domain.review.models import AcceptCard
from app.domain.room_task.models import Task
from app.domain.topic import transcript_stream
from app.domain.topic.models import RawTranscript, Topic


def _json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, default=str, ensure_ascii=False, indent=2) + "\n")


def _copy_tree(source: Path, target: Path, pattern: str = "*") -> None:
    if not source.exists():
        return
    if source.is_symlink():
        raise GatewayUnavailableError("Export cannot include symbolic links")
    root = source.resolve()
    for path in sorted(source.rglob(pattern)):
        # Do not package a symlink which could point outside the authorized root.
        if path.is_symlink():
            raise GatewayUnavailableError("Export cannot include symbolic links")
        if path.is_file():
            if not path.resolve().is_relative_to(root):
                raise GatewayUnavailableError("Export path escaped its storage root")
            dest = target / path.relative_to(source)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)


def _git(cwd: Path, *args: str, env: dict | None = None) -> bytes:
    result = subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, timeout=600
    )
    if result.returncode:
        # Provider stderr can echo credentials. Never put it in an API error.
        raise GatewayUnavailableError(
            "Repository export failed; no archive was returned"
        )
    return result.stdout


def _repository(work: Path, output: Path, url: str, username: str, token: str) -> dict:
    env = dict(os.environ)
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
    )
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "http.extraHeader"
    env["GIT_CONFIG_VALUE_0"] = (
        "Authorization: Basic "
        + base64.b64encode(f"{username}:{token}".encode()).decode()
    )
    mirror = work / "mirror.git"
    _git(work, "clone", "--mirror", "--no-local", "--", url, str(mirror), env=env)
    _git(mirror, "fsck", "--full")
    refs = _git(mirror, "for-each-ref", "--format=%(refname) %(objectname)").decode()
    if not refs.strip():
        return {"status": "empty", "refs": {}}
    # Native bundles contain Git objects, not LFS objects or submodule repos.
    # Refuse those repositories instead of promising an offline complete copy.
    changes = _git(mirror, "log", "--all", "--raw", "--format=", "--no-renames")
    if any(
        any(part.lstrip(b":") == b"160000" for part in line.split()[:2])
        for line in changes.splitlines()
        if line.startswith(b":")
    ):
        raise GatewayUnavailableError(
            "Export of submodule repositories is not supported"
        )
    # Batch metadata and candidate pointer reads: no process per Git blob, and
    # large blobs never enter Python memory.
    objects = _git(mirror, "rev-list", "--objects", "--all").splitlines()
    object_ids = b"\n".join(row.split(b" ", 1)[0] for row in objects) + b"\n"
    checked = subprocess.run(
        ["git", "cat-file", "--batch-check"],
        cwd=mirror,
        input=object_ids,
        capture_output=True,
        timeout=600,
        check=True,
    ).stdout
    candidates = [
        row.split()[0]
        for row in checked.splitlines()
        if len(row.split()) == 3
        and row.split()[1] == b"blob"
        and int(row.split()[2]) <= 1024
    ]
    with (work / "pointers").open("w+b") as pointers:
        subprocess.run(
            ["git", "cat-file", "--batch"],
            cwd=mirror,
            input=b"\n".join(candidates) + (b"\n" if candidates else b""),
            stdout=pointers,
            stderr=subprocess.DEVNULL,
            timeout=600,
            check=True,
        )
        pointers.seek(0)
        while header := pointers.readline():
            size = int(header.split()[2])
            content = pointers.read(size)
            pointers.read(1)
            if content.startswith(b"version https://git-lfs.github.com/spec/v1\n"):
                raise GatewayUnavailableError(
                    "Export requires LFS bytes; Git LFS export is not supported"
                )
    head = _git(mirror, "rev-parse", "HEAD").decode().strip()
    _git(mirror, "bundle", "create", str(output / "repository.bundle"), "--all")
    return {
        "status": "bundled",
        "head": head,
        "refs": dict(r.split(" ", 1) for r in refs.splitlines()),
    }


def _finish(output: Path, archive: Path, manifest: dict) -> None:
    entries = []
    for path in sorted(output.rglob("*")):
        if path.is_file():
            with path.open("rb") as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            entries.append(
                {
                    "path": path.relative_to(output).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": digest,
                }
            )
    manifest["files"] = entries
    _json(output / "manifest.json", manifest)
    with tarfile.open(archive, "w") as tar:
        for path in sorted(output.rglob("*")):
            if path.is_file():
                tar.add(
                    path, arcname=path.relative_to(output).as_posix(), recursive=False
                )


async def create_archive(
    project_id: uuid.UUID, db: AsyncSession, resolver: ActorResolver
) -> tuple[Path, Path]:
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    if actor.via != "token" or actor.user_id is None:
        raise AuthenticationRequiredError("Project export requires a human login")
    if await IdentityService(db).is_agent(actor.handle) or not await may_read_project(
        db, project_id=project_id, handle=actor.handle
    ):
        raise ForbiddenError("Project export requires project access")
    topics = list(await db.scalars(select(Topic).where(Topic.project_id == project_id)))
    visible = []
    for topic in topics:
        if await resolver.can_access_topic(
            actor, project_id=project_id, topic_id=topic.id
        ):
            visible.append(topic.id)
    tasks = list(
        await db.scalars(
            select(Task).where(Task.project_id == project_id, Task.room_id.in_(visible))
        )
    )
    task_places = {task.id: task.room_id for task in tasks}
    transcript_places = visible + list(task_places)
    binding = await forge.binding_for_project(project_id, db)
    minter = await forge.tokens_for_project(project_id, db) if binding else None
    repo = (
        (
            binding.url,
            "x-access-token"
            if binding.kind == "github_app"
            else binding.repo.split("/")[0],
        )
        if binding
        else None
    )
    documents = list(
        await db.scalars(
            select(Block)
            .where(
                Block.project_id == project_id,
                Block.topic_id.in_(visible),
                Block.kind.in_(
                    [
                        BlockKind.doc,
                        BlockKind.doc_node,
                        BlockKind.decision,
                        BlockKind.weekly,
                    ]
                ),
            )
            .order_by(Block.created_at)
        )
    )
    docs = [
        {
            key: getattr(row, key)
            for key in (
                "id",
                "topic_id",
                "task_id",
                "kind",
                "content",
                "doc_version",
                "struct_parent",
                "struct_order",
            )
        }
        for row in documents
    ]
    transcripts = [
        (row.id, row.topic_id, row.source, row.size, list(row.chunks))
        for row in await db.scalars(
            select(RawTranscript).where(
                RawTranscript.project_id == project_id,
                RawTranscript.topic_id.in_(transcript_places),
            )
        )
    ]
    allowed_cards = set(
        await db.scalars(select(AcceptCard.id).where(AcceptCard.topic_id.in_(visible)))
    )
    catalog = []
    copies = []
    for artifact in await artifacts.list_for_project(db, project_id):
        versions = [
            asdict(v)
            for v in await artifacts.versions(db, artifact.id)
            if v.card_id in allowed_cards
        ]
        row = asdict(artifact)
        row["versions"] = versions
        row["version"] = len(versions)
        for version in versions:
            if version["kind"] == "file" and not version["filename"]:
                raise GatewayUnavailableError(
                    "An artifact file has no retained filename"
                )
            if version["kind"] == "file" and version["filename"]:
                source = artifact_snapshot_path(
                    project_id, version["card_id"], version["filename"]
                )
                dest = f"artifacts/{artifact.id}/{version['card_id']}/{source.name}"
                copies.append((source, dest))
                version["path"] = dest
            else:
                version["offline_bytes"] = False
        catalog.append(row)
    # All database reads end before credential renewal, Git or object storage I/O.
    await db.commit()
    workspace = Path(settings.workspace_root)
    scratch = workspace / ".exports"
    scratch.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="project-", dir=scratch))
    output = work / "content"
    output.mkdir()
    try:
        manifest = {
            "format": 1,
            "project_id": str(project_id),
            "created_at": datetime.now(UTC).isoformat(),
            "scope": (
                "Caller-visible current rooms (including archived), their retained "
                "transcripts and documents; project library and published artifact "
                "catalog. Not an atomic project snapshot."
            ),
            "excluded": [
                "memory",
                "personal profiles",
                "inaccessible or deleted rooms",
                "external link bytes",
                "live device files not yet uploaded",
            ],
            "rooms": [str(t) for t in visible],
        }
        if repo:
            if minter is None:
                raise GatewayUnavailableError("Repository credentials are unavailable")
            token, _ = await minter.installation_token()
            manifest["repository"] = await run_sync(
                _repository, work, output, repo[0], repo[1], token
            )
        else:
            manifest["repository"] = {"status": "not_configured"}
        library = workspace / ".library" / str(project_id)
        manifest["library"] = {
            "status": "directory_present" if library.is_dir() else "directory_absent"
        }
        await run_sync(_copy_tree, library, output / "library")
        for topic_id in visible:
            await run_sync(
                _copy_tree,
                workspace / ".room-files" / str(project_id) / str(topic_id),
                output / "rooms" / str(topic_id) / "files",
            )
            await run_sync(
                _copy_tree,
                Path(settings.transcripts_dir) / str(project_id) / str(topic_id),
                output / "transcripts" / str(topic_id) / "legacy",
                "*.tar.gz",
            )
        for task_id, room_id in task_places.items():
            await run_sync(
                _copy_tree,
                Path(settings.transcripts_dir) / str(project_id) / str(task_id),
                output / "transcripts" / str(room_id) / "legacy-tasks" / str(task_id),
                "*.tar.gz",
            )
        _json(output / "documents.json", docs)
        for doc in docs:
            if doc["kind"] != BlockKind.doc_node:
                path = output / "documents" / f"{doc['id']}.md"
                path.parent.mkdir(exist_ok=True)
                path.write_text(doc["content"] or "")
        _json(output / "artifacts.json", catalog)
        for source, dest in copies:
            if (
                not source.is_file()
                or source.is_symlink()
                or not source.is_relative_to(
                    (workspace / ".artifacts" / str(project_id)).resolve()
                )
            ):
                raise GatewayUnavailableError(
                    "An artifact version is missing; no archive was returned"
                )
            target = output / dest
            target.parent.mkdir(parents=True, exist_ok=True)
            await run_sync(shutil.copyfile, source, target)
        transcript_index = []
        for file_id, topic_id, source, size, chunks in transcripts:
            relative = f"transcripts/{topic_id}/{file_id}.jsonl"
            target = output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            total = 0
            try:
                async with await anyio.open_file(target, "wb") as handle:
                    async for chunk in transcript_stream.contents(chunks):
                        await handle.write(chunk)
                        total += len(chunk)
            except RuntimeError as exc:
                raise GatewayUnavailableError(
                    "A transcript chunk is missing or corrupt; no archive was returned"
                ) from exc
            if total != size:
                raise GatewayUnavailableError(
                    "Transcript size mismatch; no archive was returned"
                )
            transcript_index.append(
                {
                    "id": file_id,
                    "topic_id": topic_id,
                    "source": source,
                    "path": relative,
                    "size": size,
                }
            )
        _json(output / "transcripts.json", transcript_index)
        archive = work / "project.tar"
        await run_sync(_finish, output, archive, manifest)
        return archive, work
    except BaseException:
        shutil.rmtree(work)
        raise
