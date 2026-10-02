"""Telling a team's open pages that its machines changed.

The signal carries no state: a page that hears it reads the machines again, and
one that missed it (it was reconnecting) reads them on reconnect. So writers
only note which projects changed; whoever commits announces once, per team.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.project.models import Project

_CHANGED = "machine_projects_changed"


def team_channel(team_id: int) -> str:
    """The broker channel a team's pages listen on (`routes/team_live.py`)."""
    return f"team:{team_id}"


def note_change(session: AsyncSession, project_id: uuid.UUID) -> None:
    """Record, on the session, that a machine of this project changed."""
    session.info.setdefault(_CHANGED, set()).add(project_id)


async def announce_changes(session: AsyncSession) -> None:
    """After a commit: one `machines` signal per team whose machines changed,
    naming the projects to read again."""
    projects = session.info.pop(_CHANGED, set())
    if not projects:
        return
    from app.domain.agent.runtime import get_broker

    rows = await session.execute(
        select(Project.team_id, Project.id).where(Project.id.in_(projects))
    )
    by_team: dict[int, list[str]] = {}
    for team_id, project_id in rows:
        by_team.setdefault(team_id, []).append(str(project_id))
    for team_id, project_ids in by_team.items():
        await get_broker().publish(
            team_channel(team_id),
            {"type": "state", "resource": "machines", "project_ids": project_ids},
        )
