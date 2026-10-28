"""A task that ends without its change landing closes its PR.

The platform opens a task's PR at the task's first commit (`pr_publish`), so a
task someone working it or its own session puts down would otherwise leave an open PR
that nobody is going to merge. Only the PR is closed: the branch stays, so
what was done on it is kept and a reopened task goes on from it
(`TaskService.reopen`).

A PR that merged is left as it is, and so is one already closed. Closing the
PR is not part of closing the task: a forge that cannot be reached leaves the
PR open and says so in the log, and the task is closed all the same.

A task closed because its project was archived keeps its PR open: whoever
archived the project is not the one who asked for that PR (`review.archive`).
"""

import logging
import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import background
from app.core.db import after_own_commit
from app.core.errors import UpstreamUnavailableError
from app.domain.review.forge_clients import proposal_client
from app.domain.review.github_pr import GitHubPRError
from app.domain.room_task.models import Task, TaskStatus

logger = logging.getLogger(__name__)


def close_pr_after_commit(db: AsyncSession, task: Task) -> None:
    """Once ``db`` commits the close of ``task``, close its PR if it is still
    open. A close that is rolled back closes nothing: the PR is closed only
    while the task reads as closed when the PR is looked at."""
    number = task.pr_number
    if number is None:
        return
    project_id, task_id = task.project_id, task.id
    # The committing session's own database, as a request's or a test's.
    factory = async_sessionmaker(db.bind, expire_on_commit=False)

    def committed() -> None:
        background.spawn(
            close_task_pr(factory, project_id, task_id, number),
            name="close a closed task's PR",
        )

    after_own_commit(db, committed)


async def close_task_pr(
    factory: async_sessionmaker, project_id: uuid.UUID, task_id: uuid.UUID, number: int
) -> bool:
    """Close PR ``number`` of the closed task ``task_id`` if it is open and
    unmerged. Returns whether it closed it."""
    try:
        async with factory() as session:
            task = await session.get(Task, task_id)
            if (
                task is None
                or task.status is not TaskStatus.closed
                or task.pr_number != number
            ):
                return False
            client = await proposal_client(project_id, session)
            if client is None:
                return False
            pr = await client.pr_view(number)
            if pr.get("state") != "open" or pr.get("merged"):
                return False
            await client.close_pr(number)
    except (GitHubPRError, UpstreamUnavailableError, httpx.HTTPError):
        logger.warning(
            "the PR #%s of closed task %s stays open: closing it failed",
            number,
            task_id,
            exc_info=True,
        )
        return False
    logger.info("closed PR #%s of closed task %s", number, task_id)
    return True
