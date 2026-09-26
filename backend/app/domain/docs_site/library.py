"""The docs, for AI teammates: search them, read a page.

An agent asked how to do something in 知是 should answer from the manual, not
from memory. These back the platform tools ``cheese_docs_search`` and
``cheese_docs_read`` (backend/sandbox/cheese), over the same index and ranking
as 问芝士 (``retrieval``) and the same ``.md`` twins readers can fetch.

Developer pages describe this platform's internals, so only agents working on
the platform itself see them: a project whose repository is one of
``settings.docs_dev_repositories``. Everyone else searches the public pages
only, and asking for a developer page by name is refused, not answered empty.
"""

import re
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.docs_site import access, retrieval
from app.domain.project.models import ProjectForge, ProjectGitInstallation

PAGE_CHARS = 20000
_PAGE = re.compile(r"^(dev/)?[a-z0-9-]{1,64}$")


class DevDocsForbidden(Exception):
    """A developer page asked for from a project that may not read them."""


async def reads_dev_docs(session: AsyncSession, project_id) -> bool:
    """Whether this project works on the platform's own repository."""
    allowed = {r.strip().lower() for r in settings.docs_dev_repositories if r.strip()}
    if not allowed:
        return False
    repos = [
        *(
            await session.scalars(
                select(ProjectForge.repo).where(ProjectForge.project_id == project_id)
            )
        ).all(),
        *(
            await session.scalars(
                select(ProjectGitInstallation.repo).where(
                    ProjectGitInstallation.project_id == project_id
                )
            )
        ).all(),
    ]
    return any((repo or "").strip().lower() in allowed for repo in repos)


@dataclass(frozen=True)
class Found:
    title: str
    heading: str
    url: str
    excerpt: str
    dev: bool


def _excerpt(text: str, limit: int = 280) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


async def search(query: str, *, dev: bool, limit: int = 6) -> list[Found] | None:
    """The best sections for ``query``; None when no index could be read."""
    indexes = [(await retrieval.source.get(), False)]
    if dev:
        indexes.append((await retrieval.dev_source.get(), True))
    if all(index is None for index, _ in indexes):
        return None
    hits = [
        (hit, is_dev)
        for index, is_dev in indexes
        if index is not None
        for hit in index.search(query, limit=limit)
    ]
    hits.sort(key=lambda pair: pair[0].score, reverse=True)
    return [
        Found(
            title=h.section.title,
            heading=h.section.heading,
            url=h.section.url,
            excerpt=_excerpt(h.section.text),
            dev=is_dev,
        )
        for h, is_dev in hits[:limit]
    ]


def page_slug(page: str) -> str | None:
    """``accept``, ``/docs/accept#is-merge``, ``dev/turn.md`` → the page's path
    under /docs/; None for anything that is not one."""
    slug = page.strip()
    slug = re.sub(r"^https?://[^/]+", "", slug)
    slug = slug.split("#", 1)[0].split("?", 1)[0]
    slug = slug.removeprefix("/").removeprefix("docs/").removesuffix(".md").rstrip("/")
    return slug if _PAGE.match(slug) else None


def _docs_base() -> str | None:
    url = settings.docs_index_url
    return url.rsplit("/", 1)[0] if url else None


async def read_page(
    page: str, *, dev: bool, transport: httpx.AsyncBaseTransport | None = None
) -> str | None:
    """A page's Markdown, as readers fetch it; None when there is no such page.

    Raises ``DevDocsForbidden`` for a developer page when ``dev`` is False and
    ``ValueError`` for something that is not a page name."""
    slug = page_slug(page)
    if slug is None:
        raise ValueError(f"不是文档页：{page}（写页名，如 accept 或 dev/turn）")
    if slug.startswith("dev/") and not dev:
        raise DevDocsForbidden(slug)
    base = _docs_base()
    if base is None:
        return None
    headers = (
        {"Cookie": f"{access.COOKIE}={access.internal_pass()}"}
        if slug.startswith("dev/")
        else {}
    )
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10.0), transport=transport
    ) as client:
        r = await client.get(f"{base}/{slug}.md", headers=headers)
    if r.status_code == 404 or "markdown" not in r.headers.get("content-type", ""):
        return None
    r.raise_for_status()
    text = r.text
    if len(text) > PAGE_CHARS:
        text = text[:PAGE_CHARS] + "\n\n[…本页过长，已截断]"
    return text
