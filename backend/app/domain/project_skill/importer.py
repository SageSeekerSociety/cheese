"""Read a skill made elsewhere: a SKILL.md, a zipped skill folder, or a folder on
GitHub, into the fields a project skill has.

Reading creates nothing. What comes back is laid out for a project manager to
look over before it is added, because an imported skill goes into every later
session of the project and its scripts run on the work computer.

A file that cannot be a skill file (not text, or too large) is listed in
``skipped`` rather than dropped silently, so the person sees what is left out.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import quote, urlparse

import httpx
import yaml

from app.core.errors import ValidationError
from app.core.sentences import say
from app.domain.project_skill.service import (
    MAX_FILE_BYTES,
    MAX_FILES,
    NAME,
    SKILL_FILE_SUFFIXES,
)

#: What a whole skill may weigh once unpacked; a zip claiming more is refused
#: before anything in it is read.
MAX_TOTAL_BYTES = 2_000_000
MAX_SKILL_MD_BYTES = MAX_FILE_BYTES
SCRIPT_SUFFIXES = (".py", ".sh")

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
_HEADING = re.compile(r"\A#\s+(.+?)\s*(?:\n|\Z)")
_GITHUB = re.compile(r"^/([^/]+)/([^/]+?)(?:\.git)?(?:/(tree|blob)/(.+))?/?$")


@dataclass
class ReadSkill:
    name: str
    title: str
    description: str
    body: str
    files: dict[str, str] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)

    @property
    def scripts(self) -> int:
        return sum(1 for path in self.files if path.endswith(SCRIPT_SUFFIXES))

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "body": self.body,
            "files": self.files,
            "skipped": self.skipped,
            "scripts": self.scripts,
        }


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48].strip("-")
    return slug if NAME.match(slug) else ""


def parse_skill_md(text: str, *, folder: str = "") -> ReadSkill:
    """The fields a SKILL.md carries. Its frontmatter gives the name and the
    use; the body's leading heading, when it has one, is the title."""
    meta: dict = {}
    body = text.lstrip("﻿")
    match = _FRONTMATTER.match(body)
    if match:
        try:
            loaded = yaml.safe_load(match.group(1))
        except yaml.YAMLError as exc:
            raise ValidationError(say("skillImportFrontmatter")) from exc
        meta = loaded if isinstance(loaded, dict) else {}
        body = body[match.end() :]
    body = body.strip()
    raw_name = str(meta.get("name") or folder or "").strip()
    title = raw_name
    heading = _HEADING.match(body)
    if heading:
        title = heading.group(1).strip()
        body = body[heading.end() :].strip()
    if not body:
        raise ValidationError(say("skillImportEmpty"))
    return ReadSkill(
        name=_slug(raw_name),
        title=title[:200],
        description=str(meta.get("description") or "").strip(),
        body=body,
    )


def _take(read: ReadSkill, path: str, data: bytes) -> None:
    """Keep one file beside SKILL.md, or list it as skipped."""
    if (
        PurePosixPath(path).suffix not in SKILL_FILE_SUFFIXES
        or len(data) > MAX_FILE_BYTES
        or len(read.files) >= MAX_FILES
    ):
        read.skipped.append(path)
        return
    try:
        read.files[path] = data.decode("utf-8")
    except UnicodeDecodeError:
        read.skipped.append(path)


def _safe(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return bool(parts) and not path.startswith("/") and ".." not in parts


def _root_of(paths: list[str]) -> str:
    """The folder of the shallowest SKILL.md; the skill is what sits under it."""
    found = sorted(
        (p for p in paths if PurePosixPath(p).name == "SKILL.md"),
        key=lambda p: (len(PurePosixPath(p).parts), p),
    )
    if not found:
        raise ValidationError(say("skillImportNoSkillMd"))
    parent = PurePosixPath(found[0]).parent.as_posix()
    return "" if parent == "." else parent


def _under(root: str, path: str) -> str | None:
    if not root:
        return path
    return path[len(root) + 1 :] if path.startswith(root + "/") else None


def read_upload(filename: str, data: bytes) -> ReadSkill:
    """A SKILL.md on its own, or a zip holding a skill folder."""
    if filename.lower().endswith(".zip"):
        return _read_zip(data)
    if len(data) > MAX_SKILL_MD_BYTES:
        raise ValidationError(say("skillImportTooLarge"))
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError(say("skillImportNotText")) from exc
    return parse_skill_md(text)


def _read_zip(data: bytes) -> ReadSkill:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValidationError(say("skillImportBadZip")) from exc
    entries = [
        info
        for info in archive.infolist()
        if not info.is_dir()
        and _safe(info.filename)
        and "__MACOSX" not in PurePosixPath(info.filename).parts
    ]
    root = _root_of([info.filename for info in entries])
    inside = [
        (rel, info)
        for info in entries
        if (rel := _under(root, info.filename)) is not None
    ]
    if sum(info.file_size for _, info in inside) > MAX_TOTAL_BYTES:
        raise ValidationError(say("skillImportTooLarge"))
    skill_md = next(info for rel, info in inside if rel == "SKILL.md")
    read = parse_skill_md(
        archive.read(skill_md).decode("utf-8", errors="replace"),
        folder=PurePosixPath(root).name,
    )
    for rel, info in sorted(inside, key=lambda item: item[0]):
        if rel != "SKILL.md":
            _take(read, rel, archive.read(info))
    return read


@dataclass(frozen=True)
class GithubPlace:
    owner: str
    repo: str
    ref: str
    path: str


def parse_github_url(url: str) -> GithubPlace:
    """``github.com/<owner>/<repo>[/tree|blob/<ref>/<path>]`` → where to read.

    A link to the SKILL.md itself reads the folder it sits in. A branch name
    with a slash in it is read as branch plus path, which is the one case this
    gets wrong.
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or parsed.netloc.lower() not in (
        "github.com",
        "www.github.com",
    ):
        raise ValidationError(say("skillImportGithubOnly"))
    match = _GITHUB.match(parsed.path)
    if not match:
        raise ValidationError(say("skillImportGithubOnly"))
    owner, repo, kind, rest = match.groups()
    ref, _, path = (rest or "").partition("/")
    path = path.strip("/")
    if kind == "blob" or PurePosixPath(path).name == "SKILL.md":
        path = PurePosixPath(path).parent.as_posix()
        path = "" if path == "." else path
    return GithubPlace(owner, repo, ref or "HEAD", path)


async def read_github(url: str, client: httpx.AsyncClient | None = None) -> ReadSkill:
    """A skill folder in a public GitHub repository.

    One tree listing through the API, then each file from the raw host, which
    is not counted against the API's limit for callers without a token.
    """
    place = parse_github_url(url)
    owns = client is None
    client = client or httpx.AsyncClient(timeout=20, follow_redirects=False)
    try:
        tree = await client.get(
            f"https://api.github.com/repos/{place.owner}/{place.repo}"
            f"/git/trees/{quote(place.ref, safe='')}",
            params={"recursive": "1"},
            headers={"Accept": "application/vnd.github+json"},
        )
        if tree.status_code == 404:
            raise ValidationError(say("skillImportGithubMissing"))
        if tree.status_code != 200:
            raise ValidationError(say("skillImportGithubFailed"))
        blobs = {
            item["path"]: int(item.get("size") or 0)
            for item in tree.json().get("tree", [])
            if item.get("type") == "blob" and _safe(item.get("path", ""))
        }
        prefix = f"{place.path}/" if place.path else ""
        under = [p for p in blobs if p.startswith(prefix)]
        root = place.path
        if f"{prefix}SKILL.md" not in blobs:
            raise ValidationError(say("skillImportNoSkillMd"))
        inside = {p[len(prefix) :]: blobs[p] for p in under}
        if sum(inside.values()) > MAX_TOTAL_BYTES:
            raise ValidationError(say("skillImportTooLarge"))

        async def raw(rel: str) -> bytes:
            full = f"{prefix}{rel}"
            got = await client.get(
                f"https://raw.githubusercontent.com/{place.owner}/{place.repo}"
                f"/{quote(place.ref, safe='')}/{quote(full)}"
            )
            if got.status_code != 200:
                raise ValidationError(say("skillImportGithubFailed"))
            return got.content

        read = parse_skill_md(
            (await raw("SKILL.md")).decode("utf-8", errors="replace"),
            folder=PurePosixPath(root).name or place.repo,
        )
        for rel in sorted(inside):
            if rel == "SKILL.md":
                continue
            fits = (
                PurePosixPath(rel).suffix in SKILL_FILE_SUFFIXES
                and inside[rel] <= MAX_FILE_BYTES
                and len(read.files) < MAX_FILES
            )
            if not fits:
                read.skipped.append(rel)
                continue
            _take(read, rel, await raw(rel))
        return read
    except httpx.HTTPError as exc:
        raise ValidationError(say("skillImportGithubFailed")) from exc
    finally:
        if owns:
            await client.aclose()
