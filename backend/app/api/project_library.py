"""A project's library as one reader sees it: with its read-only `题目/` or
without (`Shelf.with_task`).

`题目/` holds a task's materials, and who may read those is the task's rule
(publisher, space admin, approved participant — `task.package.may_download`), not the
project's roster: a member of the project's team who never took the task, or
someone the publisher removed, can still open the project. So every read of a
project's library is made on the shelf this module hands out for the reader.

An agent acts for the project, which the task's taker opened: its reads follow
the project owner's rule (`package.mount_for_agents`). A person's own 芝士
(`via == "delegated"`) acts for that person and reads with their rule.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.identity.actor import Actor
from app.domain.library import mount
from app.domain.library.service import library_name
from app.domain.library.shelf import Shelf
from app.domain.task import package
from app.domain.user.services import user_by_handle


async def may_read_task_files(
    db: AsyncSession, project_id: uuid.UUID, actor: Actor
) -> bool:
    """Whether ``actor`` sees `题目/` in this project's library."""
    if actor.via == "cheese":
        return await package.mount_for_agents(db, project_id)
    user_id = actor.user_id
    if user_id is None and actor.authenticated and actor.handle:
        user = await user_by_handle(db, actor.handle)
        user_id = user.id if user is not None else None
    return await package.mount_for(db, project_id, user_id)


async def shelf_for(db: AsyncSession, project_id: uuid.UUID, actor: Actor) -> Shelf:
    """The project's library as ``actor`` reads it."""
    return Shelf.of_project(
        project_id, with_task=await may_read_task_files(db, project_id, actor)
    )


async def may_read_path(
    db: AsyncSession, project_id: uuid.UUID, actor: Actor, path: str
) -> bool:
    """Whether ``actor`` may read ``path`` (a message attachment's address) as a
    file of `题目/`; False without asking when the path is not one."""
    name = library_name(path)
    if name is None or not mount.under(name):
        return False
    return await may_read_task_files(db, project_id, actor)
