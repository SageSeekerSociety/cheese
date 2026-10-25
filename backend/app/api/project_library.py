"""A project's library with its read-only `题目/` or without (`Shelf.with_task`).

`题目/` holds the materials of the task the project came from, and it follows
the one claim the project came from (`package.task_folder_shown`): while that
claim — a person's, or one team's — is live and approved, everyone in the
project and the project's agents read it; while it waits, or after it was
rejected or is no longer valid, nobody does. So every read of a project's
library is made on the shelf this module hands out.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.library import mount
from app.domain.library.service import library_name
from app.domain.library.shelf import Shelf
from app.domain.task import package


async def shelf_for(db: AsyncSession, project_id: uuid.UUID) -> Shelf:
    """The project's library as everyone in it reads it."""
    return Shelf.of_project(
        project_id, with_task=await package.task_folder_shown(db, project_id)
    )


async def may_read_path(db: AsyncSession, project_id: uuid.UUID, path: str) -> bool:
    """Whether ``path`` (a message attachment's address) is a file of `题目/`
    that this project reads; False without asking when the path is not one."""
    name = library_name(path)
    if name is None or not mount.under(name):
        return False
    return await package.task_folder_shown(db, project_id)
