"""Which 壳 is in force for a given project, and why that chain.

The inheritance is the 机构协议's, deliberately — see
`app.domain.task.protocol`. The 项目集 declares, a single 赛题 replaces the whole
key, a project's own settings outrank both, and nothing declared means
`default`. Rebuilding that chain here (a second merge with its own precedence
rules) is exactly the drift the protocol module was written to have once, so
this module resolves the NAME and steps aside.

Project settings come first because they are the one level a person can set on
the thing they are actually looking at; a 项目集 is an institution's default, not
an override of a project's own choice.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.shell.catalog import DEFAULT_SHELL_NAME, Shell, is_known, lookup
from app.domain.task.protocol import resolve as resolve_protocol

logger = logging.getLogger(__name__)

#: The key a project writes on `Project.settings`, and a 项目集 on
#: `space_categories.shell` / a 赛题 on `task.protocol_override`. It holds a NAME
#: (`"course-student"`), not a declaration.
SHELL_KEY = "shell"


def declared_name(*, settings: dict | None, protocol_shell: str | None) -> str | None:
    """The 壳 name in force: the project's own, else the protocol's.

    Split out from `resolve_shell` so the precedence is testable without a
    database — the whole chain is four lines and none of them need rows.
    """
    return (settings or {}).get(SHELL_KEY) or protocol_shell


def resolve_shell(*, project, task=None, category=None) -> Shell:
    """The 壳 ``project`` runs under, given its 赛题 and that 赛题's 项目集.

    Takes the rows rather than a session because the caller has usually loaded
    them already (and a list route must not go back to the database per project).
    """
    protocol = resolve_protocol(category=category, task=task)
    name = declared_name(
        settings=getattr(project, "settings", None), protocol_shell=protocol.shell
    )
    if name and not is_known(name):
        # Not an error: a rollout half-done or a hand-edited row must not take a
        # project's navigation down. But a teacher who picked 课程 and silently
        # got 现状 would have no way to tell, so the miss is said out loud.
        logger.warning(
            "unknown shell %r on project %s; falling back to %s",
            name,
            getattr(project, "id", "?"),
            DEFAULT_SHELL_NAME,
        )
    return lookup(name)


async def effective_shells(
    session: AsyncSession, projects: Iterable
) -> dict[uuid.UUID, Shell]:
    """Resolve for many projects in three queries, keyed by project id.

    Two of the three are skipped whenever nothing needs them: a workspace whose
    projects all declare their own 壳 (or declare none at all) reads no 赛题 and
    no 项目集.
    """
    from app.domain.space.models import SpaceCategory
    from app.domain.task.models import Task

    rows = list(projects)
    task_ids = {
        p.external_task_id for p in rows if getattr(p, "external_task_id", None)
    }
    tasks: dict[int, Task] = {}
    categories: dict[int, SpaceCategory] = {}
    if task_ids:
        found = (
            await session.execute(select(Task).where(Task.id.in_(task_ids)))
        ).scalars()
        tasks = {t.id: t for t in found}
        category_ids = {t.category_id for t in tasks.values() if t.category_id}
        if category_ids:
            cats = (
                await session.execute(
                    select(SpaceCategory).where(SpaceCategory.id.in_(category_ids))
                )
            ).scalars()
            categories = {c.id: c for c in cats}
    out: dict[uuid.UUID, Shell] = {}
    for project in rows:
        task = tasks.get(getattr(project, "external_task_id", None))
        category = categories.get(task.category_id) if task is not None else None
        out[project.id] = resolve_shell(project=project, task=task, category=category)
    return out


async def effective_shell(session: AsyncSession, project) -> Shell:
    """The 壳 one project runs under. Same chain as `effective_shells`."""
    resolved = await effective_shells(session, [project])
    return resolved[project.id]
