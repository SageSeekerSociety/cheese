"""Publish accepted static files without depending on a development machine."""

import asyncio
import hashlib
import os
import shutil
import uuid
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from app.domain.membership.services import MemberService
from app.domain.project.models import Project, ProjectRole
from app.domain.project.services import ProjectService
from app.domain.site.models import Site, SiteRelease
from app.domain.team.services import team_service
from app.domain.user.services import user_by_handle
from app.domain.workspace import service as ws

# Publication copies untrusted repository files into platform storage, so bound
# the allocation before reading blobs. These are bundle limits, not upload limits.
MAX_SITE_BYTES = 100 * 1024 * 1024
MAX_SITE_FILES = 2000
MAX_ENTRY_BYTES = 1024 * 1024
BUILD_REQUIRED = (
    "当前已采纳版本没有可直接发布的静态网站。"
    "请让芝士准备包含全部资源的静态网站，并提交验收。"
)


async def require_site_access(
    session: AsyncSession, handle: str, project_id: uuid.UUID
) -> None:
    """Private sites follow project and team membership, even with authz disabled."""
    project = await ProjectService(session).get(project_id)
    if project is None or not handle:
        raise NotFoundError("Site not found")
    if project.owner_handle == handle:
        return
    if await MemberService(session).get(project_id=project_id, user_handle=handle):
        return
    if project.team_id is not None:
        user = await user_by_handle(session, handle)
        if user is not None and await team_service(session).is_team_member(
            project.team_id, user.id
        ):
            return
    raise NotFoundError("Site not found")


async def can_publish_site(
    session: AsyncSession, handle: str, project_id: uuid.UUID
) -> bool:
    project = await ProjectService(session).get(project_id)
    if project is None or not handle:
        return False
    if project.owner_handle == handle:
        return True
    member = await MemberService(session).get(project_id=project_id, user_handle=handle)
    if member is not None and member.role == ProjectRole.lead:
        return True
    if project.team_id is not None:
        user = await user_by_handle(session, handle)
        if user is not None:
            return await team_service(session).is_team_at_least_admin(
                project.team_id, user.id
            )
    return False


async def get_current_release(
    session: AsyncSession, project_id: uuid.UUID
) -> SiteRelease | None:
    return await session.scalar(
        select(SiteRelease)
        .join(Site, Site.current_release_id == SiteRelease.id)
        .where(Site.project_id == project_id, SiteRelease.project_id == project_id)
    )


def site_metadata(release: SiteRelease) -> dict:
    return {
        "id": str(release.project_id),
        "url": f"/sites/{release.project_id}",
        "source_revision": release.source_revision,
        "directory": release.directory,
        "published_at": release.published_at.isoformat(),
        "published_by": release.published_by,
    }


def _asset_path(path: str) -> bool:
    parts = path.split("/")
    return bool(path) and all(
        part not in {"", ".", ".."}
        and not (part.startswith(".") and part != ".well-known")
        and "\\" not in part
        and "\x00" not in part
        for part in parts
    )


def _directory(value: str) -> str:
    if value == ".":
        return value
    if not _asset_path(value):
        raise ValidationError("请选择项目中的静态网站目录")
    return value


def _files_under(files: list[dict], directory: str) -> list[dict]:
    prefix = "" if directory == "." else directory + "/"
    # Dotfiles are repository metadata and credentials, not web assets.
    return [
        {**row, "relative_path": row["path"][len(prefix) :]}
        for row in files
        if row["path"].startswith(prefix) and _asset_path(row["path"][len(prefix) :])
    ]


def _validate_bundle(files: list[dict]) -> None:
    if (
        len(files) > MAX_SITE_FILES
        or sum(row["bytes"] for row in files) > MAX_SITE_BYTES
    ):
        raise ValidationError("网站最多包含 2000 个文件，总大小不能超过 100 MiB")
    if any(
        row["mode"] not in {"100644", "100755"} or row["kind"] != "blob"
        for row in files
    ):
        raise ValidationError(
            "网站目录包含符号链接或 Git 子模块，请先将资源放入目录并采纳"
        )


class _EntryReferences(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.source_files = False
        self.base: str | None = None
        self.resources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "base" and self.base is None and values.get("href") is not None:
            self.base = values["href"]
        if tag == "script" and values.get("type") in {"text/babel", "text/jsx"}:
            self.source_files = True
        resource = None
        if tag in {"script", "img", "source", "video", "audio"}:
            resource = values.get("src")
        elif tag == "link" and set((values.get("rel") or "").lower().split()) & {
            "stylesheet",
            "modulepreload",
            "icon",
        }:
            resource = values.get("href")
        if resource:
            self.resources.append(resource)


def _resolve_resource_path(path: str, base_path: str) -> str:
    if not path:
        return base_path
    parts = (
        []
        if path.startswith("/")
        else [part for part in base_path.split("/")[:-1] if part]
    )
    for part in unquote(path).split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                raise ValidationError("网站资源引用超出发布目录，请将资源放入网站目录")
            parts.pop()
        else:
            parts.append(part)
    resolved = "/".join(parts)
    return resolved + "/" if path.endswith("/") else resolved


def _validate_entry_resources(parser: _EntryReferences, files: list[dict]) -> None:
    paths = {row["relative_path"] for row in files}
    try:
        base = urlsplit((parser.base or "").strip())
        external_base = bool(base.scheme or base.netloc)
        base_path = (
            "index.html"
            if external_base
            else _resolve_resource_path(base.path, "index.html")
        )
        for reference in parser.resources:
            url = urlsplit(reference.strip())
            if url.scheme or url.netloc or external_base:
                continue
            path = _resolve_resource_path(url.path, base_path)
            if PurePosixPath(path).suffix.lower() in {
                ".ts",
                ".tsx",
                ".jsx",
                ".vue",
                ".scss",
                ".sass",
                ".less",
            }:
                raise ValidationError(BUILD_REQUIRED)
            if path.endswith("/"):
                path += "index.html"
            if path not in paths:
                raise ValidationError(
                    f"网站缺少资源：{path}。请将资源放入网站目录并采纳后再发布"
                )
    except ValueError as exc:
        raise ValidationError("网站 HTML 中的资源地址格式无效") from exc


def _static_entry(files: list[dict], html: bytes) -> bool:
    if any(
        row["relative_path"] == "package.json"
        or PurePosixPath(row["relative_path"]).suffix.lower()
        in {".ts", ".tsx", ".jsx", ".vue"}
        for row in files
    ):
        return False
    parser = _EntryReferences()
    try:
        parser.feed(html.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise ValidationError("网站 HTML 中的资源地址格式无效") from exc
    _validate_entry_resources(parser, files)
    return not parser.source_files


def publication_source(project_id: uuid.UUID) -> dict:
    revision = ws.accepted_revision(project_id)
    files = ws.committed_files(project_id, revision)
    entries = [
        row
        for row in files
        if PurePosixPath(row["path"]).name == "index.html"
        and row["mode"] in {"100644", "100755"}
        and row["bytes"] <= MAX_ENTRY_BYTES
        and _asset_path(row["path"])
    ]
    candidates = []
    for entry in entries:
        directory = str(PurePosixPath(entry["path"]).parent)
        bundle = _files_under(files, directory)
        try:
            _validate_bundle(bundle)
        except ValidationError:
            continue
        html = ws.read_committed_blobs(project_id, [entry["oid"]])[entry["oid"]]
        try:
            if _static_entry(bundle, html):
                candidates.append({"directory": directory, "entry_file": entry["path"]})
        except ValidationError:
            continue
    return {"source_revision": revision, "candidates": candidates}


def release_directory(release: SiteRelease) -> Path:
    return (
        Path(settings.workspace_root).resolve()
        / ".sites"
        / str(release.project_id)
        / str(release.id)
    )


def read_release_file(release: SiteRelease, path: str) -> bytes | None:
    path = path or release.entry_file
    if path.endswith("/"):
        path += "index.html"
    if not _asset_path(path) or path not in release.manifest:
        return None
    root = release_directory(release)
    target = (root / path).resolve()
    if root not in target.parents:
        return None
    try:
        return target.read_bytes()
    except FileNotFoundError:
        return None


def _snapshot(
    project_id: uuid.UUID, revision: str, directory: str, release_id: uuid.UUID
) -> dict:
    files = _files_under(ws.committed_files(project_id, revision), directory)
    _validate_bundle(files)
    entry = next((row for row in files if row["relative_path"] == "index.html"), None)
    if entry is None or entry["bytes"] > MAX_ENTRY_BYTES:
        raise ValidationError(BUILD_REQUIRED)
    blobs = ws.read_committed_blobs(project_id, [row["oid"] for row in files])
    if not _static_entry(files, blobs[entry["oid"]]):
        raise ValidationError(BUILD_REQUIRED)
    root = Path(settings.workspace_root).resolve() / ".sites" / str(project_id)
    staging = root / f".staging-{release_id}"
    destination = root / str(release_id)
    staging.mkdir(parents=True)
    manifest = {}
    try:
        for row in files:
            data = blobs[row["oid"]]
            path = row["relative_path"]
            target = staging / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            manifest[path] = {
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        os.replace(staging, destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return manifest


async def publish_site(
    session: AsyncSession,
    project_id: uuid.UUID,
    *,
    handle: str,
    directory: str,
    expected_source_revision: str,
) -> SiteRelease:
    await require_site_access(session, handle, project_id)
    if not await can_publish_site(session, handle, project_id):
        raise ForbiddenError("只有项目负责人或团队管理员可以发布网站")
    directory = _directory(directory)
    # Serialize even a project's first publication; no Site row exists to lock yet.
    await session.scalar(
        select(Project).where(Project.id == project_id).with_for_update()
    )
    revision = await asyncio.to_thread(ws.accepted_revision, project_id)
    if revision != expected_source_revision:
        raise ConflictError("项目已采纳的版本发生变化，请刷新后再发布")
    release_id = uuid.uuid4()
    manifest = await asyncio.to_thread(
        _snapshot, project_id, revision, directory, release_id
    )
    release = SiteRelease(
        id=release_id,
        project_id=project_id,
        source_revision=revision,
        directory=directory,
        entry_file="index.html",
        manifest=manifest,
        published_at=datetime.now(UTC),
        published_by=handle,
    )
    session.add(release)
    await session.flush()
    site = await session.get(Site, project_id)
    if site is None:
        session.add(Site(project_id=project_id, current_release_id=release_id))
    else:
        site.current_release_id = release_id
    await session.flush()
    return release
