"""The mentors a task's publisher chose (#3830).

A task may name people who join every claimer's project as mentors: they read
the project, speak and comment in it, and change nothing. A mentor reads a
student's work, so only the people running the course may be one — the task's
publisher or an admin of its space — and a student claims such a task only
after agreeing to them. Projects already made keep the mentors they have.
"""

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import task_roles as task_seats
from app.core.errors import BadRequestError
from app.core.sentences import say
from app.domain.task.models import Task
from app.domain.task.repositories import TaskMentorRepository
from app.domain.user.services import users_by_handle


async def set_mentors(
    session: AsyncSession,
    *,
    task: Task,
    handles: Sequence[str],
    creator_user_id: int | None,
) -> None:
    """Record who joins every claimer's project as a mentor.

    Two reads for the whole list, not two per handle: the accounts in one query
    and the board's admins in another. Handles are still checked in the order
    given, so the first bad one is the one reported, as before.
    """
    users = await users_by_handle(session, handles)
    admin_ids: set[int] = set()
    if any(user.id != creator_user_id for user in users.values()):
        admin_ids = await task_seats.space_teachers(session, task.space_id)
    for handle in handles:
        user = users.get(handle)
        if user is None:
            raise BadRequestError(say("accountNotFound", handle=handle))
        if user.id != creator_user_id and user.id not in admin_ids:
            raise BadRequestError(say("mentorNotSpaceAdmin", handle=handle))
    await TaskMentorRepository(session).replace(task.id, handles)


async def mentors_of(session: AsyncSession, task_id: int) -> list[str]:
    return await TaskMentorRepository(session).of_task(task_id)


async def require_consent(session: AsyncSession, *, task: Task, agreed: bool) -> None:
    """A task with mentors is claimed only by someone who agreed to them."""
    if not agreed and await mentors_of(session, task.id):
        raise BadRequestError(say("mentorConsentRequired"))
