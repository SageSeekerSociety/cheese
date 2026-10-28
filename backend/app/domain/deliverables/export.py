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
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from anyio.to_thread import run_sync
from sqlalchemy import DateTime, String, Uuid, column, select, table, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import (
    GatewayUnavailableError,
    NotFoundError,
    UpstreamUnavailableError,
)
from app.domain.block.models import Block, BlockKind
from app.domain.conversation.services import of_rooms, room_column
from app.domain.deliverables import artifacts
from app.domain.identity.actor import Actor
from app.domain.living_doc.models import Document, DocumentNode
from app.domain.project import forge
from app.domain.project.ports import FileStore, LibraryFiles
from app.domain.topic.models import Topic

# Which documents tasks keep. A bare table: ``room_task`` depends on this
# domain.
_tasks = table(
    "tasks", column("id", Uuid), column("room_id", Uuid), column("document_id", Uuid)
)

# The cards filed in each room. Bare for the same sibling reason: ``review``
# reads this package (the list of what a room has delivered), so reading its
# model back from here would close a cycle (``.importlinter``, C3).
_cards = table(
    "accept_cards",
    column("id", Uuid),
    column("topic_id", Uuid),
    column("artifact_id", Uuid),
    column("status", String),
    column("change_subject", String),
    column("decided_by", String),
    column("decided_at", DateTime(timezone=True)),
    column("created_at", DateTime(timezone=True)),
    column("deliverable_kind", String),
    column("deliverable_name", String),
    column("deliverable_url", String),
    column("pr_head_sha", String),
)

# The goals document a project names. Also bare, so the read below sees a
# pointer written by ``living_doc.overview``: that write goes through the table,
# so a ``Project`` row this session already holds still says None.
_projects = table("projects", column("id", Uuid), column("overview_document_id", Uuid))


async def _versions_of(
    db: AsyncSession, artifact_ids: Sequence[uuid.UUID], hidden: set[uuid.UUID]
) -> dict[uuid.UUID, list[artifacts.ArtifactVersion]]:
    """每一项交付过的每一版，一次查回来再按项分组 —— ``artifacts.versions`` 的批量版，
    同样的筛选和排序，版号在各自那一项里从 1 数起。"""
    grouped: dict[uuid.UUID, list[artifacts.ArtifactVersion]] = {
        artifact_id: [] for artifact_id in artifact_ids
    }
    if not artifact_ids:
        return grouped
    found = await db.execute(
        select(_cards)
        .where(
            _cards.c.artifact_id.in_(list(artifact_ids)),
            _cards.c.status == "accepted",
            _cards.c.topic_id.not_in(list(hidden)) if hidden else true(),
        )
        .order_by(_cards.c.decided_at, _cards.c.created_at)
    )
    for row in found.all():
        bucket = grouped[row.artifact_id]
        bucket.append(
            artifacts.ArtifactVersion(
                number=len(bucket) + 1,
                card_id=row.id,
                subject=row.change_subject,
                delivered_at=row.decided_at,
                decided_by=row.decided_by,
                kind=row.deliverable_kind,
                filename=row.deliverable_name,
                url=row.deliverable_url,
                revision=row.pr_head_sha,
                room_id=row.topic_id,
            )
        )
    return grouped


def _json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, default=str, ensure_ascii=False, indent=2) + "\n")


def _copy_tree(source: Path, target: Path) -> None:
    if not source.exists():
        return
    if source.is_symlink():
        raise GatewayUnavailableError("Export cannot include symbolic links")
    root = source.resolve()
    for path in sorted(source.rglob("*")):
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


class TopicReadability(Protocol):
    """Which of these rooms the caller may read.

    The one thing this module needs from the request's credential, named here
    rather than imported: ``app.api.auth`` builds the resolver, so a domain
    importing it would reach up into the layer above (``.importlinter``, C1).
    The route resolves the credential, decides what it may read, and passes
    itself in for this last question — the room list is already in hand here.
    """

    async def readable_topic_ids(
        self, actor: Actor, *, project_id: uuid.UUID, topics: Sequence[Topic]
    ) -> set[uuid.UUID]: ...


async def create_archive(
    project_id: uuid.UUID,
    db: AsyncSession,
    actor: Actor,
    readability: TopicReadability,
    *,
    file_store: FileStore,
    library_files: LibraryFiles,
) -> tuple[Path, Path]:
    """把这个项目读得出来的东西打成一个压缩包，交回 ``(内容, 压缩包)`` 两条路径。

    ``file_store`` 和 ``library_files`` 是递进来的：一版交付物留存下来的那份字节在
    哪儿、这个项目的资料库里都有哪些文件，都是第 4 层 `library` 的事，本包够不着
    （`ports.py`）。两样都要，是因为搬字节分两段：路径在事务里定下来（
    ``artifact_snapshot_path`` / ``stored``），字节在事务结束之后、git 那句之后才读。
    """
    topics = list(await db.scalars(select(Topic).where(Topic.project_id == project_id)))
    readable = await readability.readable_topic_ids(
        actor, project_id=project_id, topics=topics
    )
    visible = [topic.id for topic in topics if topic.id in readable]
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
    room = room_column(Block.conversation_id)
    weeklies = list(
        await db.execute(
            select(Block, room)
            .where(
                Block.project_id == project_id,
                of_rooms(Block.conversation_id, visible),
                Block.kind == BlockKind.weekly,
            )
            .order_by(Block.created_at)
        )
    )
    # The project's goals and the living documents of the tasks in the
    # channels the caller reads, with their node trees: a document is `doc`,
    # its blocks are `doc_node` under it.
    # The pointer, read from the table rather than off the row (the bare-table
    # comment above says why), and not through ``living_doc.overview``: that
    # module is one layer up, and this import would be a new upward edge.
    pointed = (
        await db.execute(
            select(_projects.c.overview_document_id).where(_projects.c.id == project_id)
        )
    ).scalar_one()
    overview = await db.get(Document, pointed) if pointed is not None else None
    kept: list[tuple] = [(overview, None, None)] if overview else []
    task_rows = (
        await db.execute(
            select(_tasks.c.id, _tasks.c.room_id, _tasks.c.document_id)
            .where(_tasks.c.room_id.in_(visible), _tasks.c.document_id.is_not(None))
            .order_by(_tasks.c.id)
        )
    ).all()
    # One query for every task's document and one for all their node trees,
    # not a pair per task.
    doc_ids = [document_id for _, _, document_id in task_rows]
    found_docs = {
        doc.id: doc
        for doc in await db.scalars(select(Document).where(Document.id.in_(doc_ids)))
    }
    if overview:
        doc_ids.append(overview.id)
    nodes_of: dict[uuid.UUID, list[DocumentNode]] = {}
    for node in await db.scalars(
        select(DocumentNode)
        .where(DocumentNode.document_id.in_(doc_ids))
        .order_by(DocumentNode.position)
    ):
        nodes_of.setdefault(node.document_id, []).append(node)
    for task_id, room_id, document_id in task_rows:
        if (doc := found_docs.get(document_id)) is not None:
            kept.append((doc, room_id, task_id))
    docs: list[dict] = []
    for doc, room_id, task_id in kept:
        docs.append(
            {
                "id": doc.id,
                "topic_id": room_id,
                "task_id": task_id,
                "kind": "doc",
                "content": doc.content,
                "doc_version": doc.version,
                "struct_parent": None,
                "struct_order": None,
            }
        )
        docs.extend(
            {
                "id": node.id,
                "topic_id": room_id,
                "task_id": task_id,
                "kind": "doc_node",
                "content": node.content,
                "doc_version": None,
                "struct_parent": doc.id,
                "struct_order": node.position,
            }
            for node in nodes_of.get(doc.id, [])
        )
    docs.extend(
        {
            "id": row.id,
            "topic_id": room_id,
            "task_id": row.conversation_id if row.conversation_id != room_id else None,
            "kind": "weekly",
            "content": row.content,
            "doc_version": None,
            "struct_parent": None,
            "struct_order": None,
        }
        for row, room_id in weeklies
    )
    allowed_cards = set(
        await db.scalars(select(_cards.c.id).where(_cards.c.topic_id.in_(visible)))
    )
    catalog = []
    copies = []
    unseen = {topic.id for topic in topics if topic.members_only} - set(visible)
    listed = await artifacts.list_for_project(db, project_id, hidden=unseen)
    every_version = await _versions_of(db, [a.id for a in listed], unseen)
    for artifact in listed:
        versions = [
            asdict(v) for v in every_version[artifact.id] if v.card_id in allowed_cards
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
                source = file_store.artifact_snapshot_path(
                    project_id, version["card_id"], version["filename"]
                )
                dest = f"artifacts/{artifact.id}/{version['card_id']}/{source.name}"
                copies.append((source, dest))
                version["path"] = dest
            else:
                version["offline_bytes"] = False
        catalog.append(row)
    stored_files = await library_files.stored(db, project_id)
    # All database reads end before credential renewal or Git I/O.
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
                "Caller-visible current rooms (including archived) and their "
                "documents; project library and published artifact catalog. "
                "Not an atomic project snapshot."
            ),
            "excluded": [
                "memory",
                "personal profiles",
                "inaccessible or deleted rooms",
                "external link bytes",
                "agent session transcripts",
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
        manifest["library"] = {"files": len(stored_files)}
        for name, location, key in stored_files:
            target = output / "library" / name
            if not target.resolve().is_relative_to((output / "library").resolve()):
                raise UpstreamUnavailableError("Export path escaped its storage root")
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                await library_files.save_blob(location, key, target)
            except NotFoundError as exc:
                raise UpstreamUnavailableError(
                    "A library file is missing; no archive was returned"
                ) from exc
        for topic_id in visible:
            await run_sync(
                _copy_tree,
                workspace / ".room-files" / str(project_id) / str(topic_id),
                output / "rooms" / str(topic_id) / "files",
            )
        _json(output / "documents.json", docs)
        for doc in docs:
            if doc["kind"] != "doc_node":
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
        archive = work / "project.tar"
        await run_sync(_finish, output, archive, manifest)
        return archive, work
    except BaseException:
        shutil.rmtree(work)
        raise
