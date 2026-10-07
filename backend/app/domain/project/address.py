"""Addresses people can say: a project's slug and the numbers inside it.

`/projects/<slug>/tasks/318` names the same task as its UUID does. The UUID
stays the key everywhere; the slug and the number are names for people, so
nothing outside an address ever stores them.

- A project's slug is unique across all projects. It starts as eight random
  characters (`random_slug`) and its managers may change it. The names it had
  before stay in `project_slugs`, so an old link still finds it and no other
  project can take that name.
- Tasks, documents of the project's own and channels are each numbered from 1
  within their project. A number is taken in the transaction that creates the
  thing, from that project's counter row for that kind, so two projects never
  wait on each other and a rollback costs a gap, never a duplicate.
"""

import enum
import re
import uuid

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.project.models import Project, ProjectCounter, ProjectSlug


class Numbered(enum.StrEnum):
    """What a project numbers. The value is the counter's key and the address
    segment: `/projects/<slug>/<value>/<number>`."""

    task = "tasks"
    document = "docs"
    channel = "channels"


SLUG_MIN = 3
SLUG_MAX = 32
_SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")

# Words kept back for pages that may one day sit at `/projects/<word>`, where
# a project of that name would shadow them.
RESERVED_SLUGS = frozenset({"new"})


class SlugError(ValueError):
    """Why a requested slug cannot be this project's. ``reason`` is one of
    ``format`` / ``reserved`` / ``taken``."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def slug_problem(slug: str) -> str | None:
    """What is wrong with ``slug`` on its own, before anyone else's names are
    consulted: ``format`` or ``reserved``, or None."""
    if not SLUG_MIN <= len(slug) <= SLUG_MAX or not _SLUG.fullmatch(slug):
        return "format"
    if slug in RESERVED_SLUGS:
        return "reserved"
    return None


def looks_like_uuid(ref: str) -> uuid.UUID | None:
    """The UUID ``ref`` spells, if it spells one. A slug is at most 32
    characters and a UUID's text is 36, so the two never overlap."""
    try:
        return uuid.UUID(ref)
    except ValueError:
        return None


async def _holder_of(session: AsyncSession, slug: str) -> uuid.UUID | None:
    """The project that goes, or went, by ``slug``."""
    current = await session.scalar(select(Project.id).where(Project.slug == slug))
    if current is not None:
        return current
    return await session.scalar(
        select(ProjectSlug.project_id).where(ProjectSlug.slug == slug)
    )


async def rename_slug(session: AsyncSession, project: Project, slug: str) -> str:
    """Make ``slug`` the project's name in addresses. Its old name keeps
    leading here. Raises `SlugError`."""
    slug = slug.strip().lower()
    problem = slug_problem(slug)
    if problem is not None:
        raise SlugError(problem)
    if slug == project.slug:
        return slug
    holder = await _holder_of(session, slug)
    if holder is not None and holder != project.id:
        raise SlugError("taken")
    # Taking one of its own old names back: it is current again, not former.
    await session.execute(
        delete(ProjectSlug).where(
            ProjectSlug.slug == slug, ProjectSlug.project_id == project.id
        )
    )
    session.add(ProjectSlug(slug=project.slug, project_id=project.id))
    project.slug = slug
    await session.flush()
    return slug


async def project_by_ref(session: AsyncSession, ref: str) -> Project | None:
    """The project ``ref`` names: its id, its slug, or a slug it once had."""
    project_id = looks_like_uuid(ref)
    if project_id is None:
        project_id = await _holder_of(session, ref.lower())
    if project_id is None:
        return None
    return await session.get(Project, project_id)


async def take_number(
    session: AsyncSession, project_id: uuid.UUID, kind: Numbered
) -> int:
    """The next number for a ``kind`` in this project. Call it in the
    transaction that creates the thing: it locks this one counter row until
    that transaction ends."""
    stmt = (
        insert(ProjectCounter)
        .values(project_id=project_id, kind=kind.value, last_number=1)
        .on_conflict_do_update(
            index_elements=[ProjectCounter.project_id, ProjectCounter.kind],
            set_={"last_number": ProjectCounter.last_number + 1},
        )
        .returning(ProjectCounter.last_number)
    )
    return (await session.execute(stmt)).scalar_one()
