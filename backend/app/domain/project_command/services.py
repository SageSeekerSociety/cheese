"""Fixing a command under a name in a project, and taking it off again."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.project_command.models import COMMAND_MAX, NAME_MAX, ProjectCommand

__all__ = ["COMMAND_MAX", "NAME_MAX", "as_dict", "fix", "of_project", "remove"]


async def of_project(db: AsyncSession, project_id: uuid.UUID) -> list[dict]:
    """The project's named commands, by name."""
    rows = await db.scalars(
        select(ProjectCommand)
        .where(ProjectCommand.project_id == project_id)
        .order_by(ProjectCommand.name)
    )
    return [as_dict(row) for row in rows]


async def fix(
    db: AsyncSession, project_id: uuid.UUID, *, name: str, command: str, by: str
) -> dict:
    """`command` under `name` in the project; a name it already has now names
    this command."""
    now = datetime.now(UTC)
    statement = insert(ProjectCommand).values(
        id=uuid.uuid4(),
        project_id=project_id,
        name=name,
        command=command,
        fixed_by=by,
        fixed_at=now,
    )
    row = await db.scalar(
        statement.on_conflict_do_update(
            constraint="uq_project_commands_name",
            set_={"command": command, "fixed_by": by, "fixed_at": now},
        )
        .returning(ProjectCommand)
        .execution_options(populate_existing=True)
    )
    assert row is not None
    return as_dict(row)


async def remove(
    db: AsyncSession, project_id: uuid.UUID, command_id: uuid.UUID
) -> None:
    """Takes a named command off the project; one already gone changes nothing."""
    await db.execute(
        delete(ProjectCommand).where(
            ProjectCommand.project_id == project_id, ProjectCommand.id == command_id
        )
    )


def as_dict(row: ProjectCommand) -> dict:
    """A named command as a reader is handed it."""
    return {
        "id": str(row.id),
        "name": row.name,
        "command": row.command,
        "fixed_by": row.fixed_by,
        "fixed_at": row.fixed_at.isoformat(),
    }
