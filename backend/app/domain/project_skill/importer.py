"""Read a skill made elsewhere: a SKILL.md, a zipped skill folder, or a folder on
GitHub, into the fields a project skill has.

Reading creates nothing. What comes back is laid out for a project manager to
look over before it is added, because an imported skill goes into every later
session of the project and its scripts run on the work computer.

A file that cannot be a skill file (not text, or too large) is listed in
``skipped`` rather than dropped silently, so the person sees what is left out.
"""

from __future__ import annotations

import base64
import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import quote, urlparse

import httpx
import yaml

from app.core.config import settings
from app.core.errors import UnprocessableEntityError
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
    #: What the reader wants the person to know about the reading itself — a
    #: repository tree cut short, say. Not ``skipped``: these are not files that
    #: were left out, they are the reasons the reading is incomplete.
    notes: list[str] = field(default_factory=list)
    #: The SKILL.md exactly as it was read. Kept for a caller that *lands* the
    #: skill (the machine's web direct-connect writes it back out), and kept out
    #: of :meth:`to_json` on purpose: the project import preview does not carry
    #: the raw text.
    raw: str = ""

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
            "notes": self.notes,
            "scripts": self.scripts,
        }


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48].strip("-")
    return slug if NAME.match(slug) else ""


def parse_skill_md(
    text: str, *, folder: str = "", keep_name: bool = False
) -> ReadSkill:
    """The fields a SKILL.md carries. Its frontmatter gives the name and the
    use; the body's leading heading, when it has one, is the title.

    ``keep_name`` skips the slugging: a skill being *landed* somewhere else
    keeps the name it advertises (the agentskills.io rules allow uppercase),
    where the default folds it into the project skill's lowercase shape.
    """
    meta: dict = {}
    body = text.lstrip("﻿")
    match = _FRONTMATTER.match(body)
    if match:
        try:
            loaded = yaml.safe_load(match.group(1))
        except yaml.YAMLError as exc:
            raise UnprocessableEntityError(say("skillImportFrontmatter")) from exc
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
        raise UnprocessableEntityError(say("skillImportEmpty"))
    return ReadSkill(
        name=raw_name if keep_name else _slug(raw_name),
        title=title[:200],
        description=str(meta.get("description") or "").strip(),
        body=body,
        raw=text,
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
        raise UnprocessableEntityError(say("skillImportNoSkillMd"))
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
        raise UnprocessableEntityError(say("skillImportTooLarge"))
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnprocessableEntityError(say("skillImportNotText")) from exc
    return parse_skill_md(text)


def _read_zip(data: bytes) -> ReadSkill:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UnprocessableEntityError(say("skillImportBadZip")) from exc
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
        raise UnprocessableEntityError(say("skillImportTooLarge"))
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
        raise UnprocessableEntityError(say("skillImportGithubOnly"))
    match = _GITHUB.match(parsed.path)
    if not match:
        raise UnprocessableEntityError(say("skillImportGithubOnly"))
    owner, repo, kind, rest = match.groups()
    ref, _, path = (rest or "").partition("/")
    path = path.strip("/")
    if kind == "blob" or PurePosixPath(path).name == "SKILL.md":
        path = PurePosixPath(path).parent.as_posix()
        path = "" if path == "." else path
    return GithubPlace(owner, repo, ref or "HEAD", path)


#: Hosts a skill may be fetched from. An arbitrary host is refused: naming one
#: would turn this route into a fetch-anything probe (SSRF).
_SKILL_HOSTS = ("github.com", "www.github.com")
_RAW_HOSTS = ("raw.githubusercontent.com",)


def normalize_skill_url(url: str) -> str:
    """A skill's address as a ``github.com`` folder link the reader understands.

    A GitHub link is taken as it is. A ``raw.githubusercontent.com`` link to a
    SKILL.md is read back into the folder it sits in — reading the folder, not
    the one file, is what brings a skill's ``scripts/`` along with it.
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise UnprocessableEntityError(say("skillWebUrlInvalid"))
    host = parsed.netloc.lower()
    if host in _SKILL_HOSTS:
        parse_github_url(url)  # says its own refusal for anything malformed
        return url.strip()
    if host not in _RAW_HOSTS:
        raise UnprocessableEntityError(say("skillWebUrlInvalid"))
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 4 or PurePosixPath(parts[-1]).name != "SKILL.md":
        raise UnprocessableEntityError(say("skillWebUrlInvalid"))
    owner, repo, ref = parts[0], parts[1], parts[2]
    folder = PurePosixPath("/".join(parts[3:])).parent.as_posix()
    where = "" if folder == "." else f"/{folder}"
    normalized = f"https://github.com/{owner}/{repo}/tree/{ref}{where}"
    parse_github_url(normalized)  # the same shape check, said the same way
    return normalized


def github_headers() -> dict[str, str]:
    """The headers every ``api.github.com`` read goes out with.

    Unauthenticated, GitHub answers 60 requests an hour per address, and the
    skills market spends a good part of that before anyone clicks install. A
    read-only token (``settings.github_token``) lifts it to 5000; unset, the
    same reads still work and still cost the 60.
    """
    headers = {"Accept": "application/vnd.github+json"}
    token = settings.github_token.strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def fetch_file(
    client: httpx.AsyncClient, owner: str, repo: str, ref: str, path: str
) -> bytes | None:
    """One file's bytes out of a repository, over the API host.

    ``raw.githubusercontent.com`` is not reachable on every network — measured
    2026-10-11: ten of twelve files of one public skill timed out while
    ``api.github.com`` answered in half a second — so a file is read through the
    contents endpoint and its base64 payload decoded here. ``None`` when it is
    not there: an absent README is a fact a caller may act on, not an error.

    ``ponytail:`` this spends one of the unauthenticated API's 60 requests an
    hour per file, where the raw host spent none. Upgrade path: send a token, or
    probe the raw host once and prefer it when it answers.
    """
    got = await client.get(
        f"https://api.github.com/repos/{owner}/{repo}/contents/{quote(path)}",
        params={"ref": ref},
        headers=github_headers(),
    )
    if got.status_code != 200:
        return None
    payload = got.json()
    if not isinstance(payload, dict):
        return None  # a directory comes back as a listing, not one file
    if payload.get("encoding") != "base64":
        return b""  # an empty file comes back with neither content nor encoding
    return base64.b64decode(payload.get("content") or "")


def _skill_roots(blobs: dict[str, int], prefix: str) -> list[str]:
    """The folders under ``prefix`` to read as skills, each ending in a slash.

    The folder itself when it holds a ``SKILL.md``; otherwise the folders one
    level down that do (``skills/<name>/SKILL.md``) — how a plugin bundles its
    skills, and what lets a plugin be installed here like a skill.
    """
    if f"{prefix}SKILL.md" in blobs:
        return [prefix]
    marker = f"{prefix}skills/"
    return sorted(
        {
            path[: -len("SKILL.md")]
            for path in blobs
            if path.startswith(marker)
            and path.endswith("/SKILL.md")
            # One level only: the name between ``skills/`` and the file. The
            # slice has to drop the file's own leading slash too, or every name
            # ends in ``/`` and the whole set falls out.
            and "/" not in path[len(marker) : -len("/SKILL.md")]
        }
    )


async def _one_skill(
    client: httpx.AsyncClient,
    place: GithubPlace,
    blobs: dict[str, int],
    root: str,
    *,
    keep_name: bool,
) -> ReadSkill:
    """One skill at ``root`` (which ends in a slash), its files read whole."""
    inside = {path[len(root) :]: blobs[path] for path in blobs if path.startswith(root)}
    if sum(inside.values()) > MAX_TOTAL_BYTES:
        raise UnprocessableEntityError(say("skillImportTooLarge"))

    async def raw(rel: str) -> bytes:
        data = await fetch_file(
            client, place.owner, place.repo, place.ref, f"{root}{rel}"
        )
        if data is None:
            raise UnprocessableEntityError(say("skillImportGithubFailed"))
        return data

    read = parse_skill_md(
        (await raw("SKILL.md")).decode("utf-8", errors="replace"),
        folder=PurePosixPath(root.rstrip("/")).name or place.repo,
        keep_name=keep_name,
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


async def read_github_many(
    url: str, client: httpx.AsyncClient | None = None, *, keep_name: bool = False
) -> list[ReadSkill]:
    """Every skill the folder at ``url`` holds, in a public GitHub repository.

    One tree listing through the API, then each file through the API too
    (:func:`fetch_file`) — the raw host is unreachable on some networks. A
    folder holding a ``SKILL.md`` is one skill; one holding none but carrying
    ``skills/<name>/SKILL.md`` underneath is a plugin, and each of those is read
    as its own skill.
    """
    place = parse_github_url(url)
    owns = client is None
    client = client or httpx.AsyncClient(timeout=20, follow_redirects=False)
    try:
        tree = await client.get(
            f"https://api.github.com/repos/{place.owner}/{place.repo}"
            f"/git/trees/{quote(place.ref, safe='')}",
            params={"recursive": "1"},
            headers=github_headers(),
        )
        if tree.status_code == 404:
            raise UnprocessableEntityError(say("skillImportGithubMissing"))
        if tree.status_code != 200:
            raise UnprocessableEntityError(say("skillImportGithubFailed"))
        blobs = {
            item["path"]: int(item.get("size") or 0)
            for item in tree.json().get("tree", [])
            if item.get("type") == "blob" and _safe(item.get("path", ""))
        }
        prefix = f"{place.path}/" if place.path else ""
        roots = _skill_roots(blobs, prefix)
        if not roots:
            raise UnprocessableEntityError(say("skillImportNoSkillMd"))
        return [
            await _one_skill(client, place, blobs, root, keep_name=keep_name)
            for root in roots
        ]
    except httpx.HTTPError as exc:
        raise UnprocessableEntityError(say("skillImportGithubFailed")) from exc
    finally:
        if owns:
            await client.aclose()


async def read_github(
    url: str, client: httpx.AsyncClient | None = None, *, keep_name: bool = False
) -> ReadSkill:
    """The first skill the folder at ``url`` holds — the one-skill reading.

    Callers that mean 「one skill at this address」 keep their old meaning, error
    keys included; a folder with none still says ``skillImportNoSkillMd``.
    """
    return (await read_github_many(url, client, keep_name=keep_name))[0]
