"""An Office file in a task, compared with the version it should be read against.

A first delivery is read against what the project last took in: the commit the
task's work started from. One handed over again after a 退回 is read against the
version that was sent back, so what shows is what changed in answer to the
comments — the commit those comments were written on.
"""

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.documents.compare import compare, kind_of
from app.domain.repository.forge_files import ProjectFiles
from app.domain.review.comments import ReviewCommentService
from app.domain.room_task.models import Task


async def comparison(session: AsyncSession, task: Task, path: str) -> dict | None:
    """None when the file is not a Word document, workbook or deck, or either
    version cannot be read."""
    if kind_of(path) is None:
        return None
    files = ProjectFiles(session, task.project_id, task.id)
    new, _source = await files.raw(path, "committed")
    returned = next(
        (
            row.commit_sha
            for row in await ReviewCommentService(session).last_round(task.id)
            if row.commit_sha
        ),
        None,
    )
    base = returned or await files.base_revision()
    old = await files.raw_at(path, base) if base else None
    result = await asyncio.to_thread(compare, old, new, path)
    if result is None:
        return None
    return {**result, "against": "returned" if returned else "taken", "base": base}
