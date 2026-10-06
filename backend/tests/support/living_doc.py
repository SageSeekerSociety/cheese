"""Setting up a living document in a test.

A document version is recorded one way only: a store from the collaboration
service. Tests that need a document to exist record one the same way.

Only a task has a living document; a channel has none. A test about documents
in general opens a task (``open_task`` in ``tests/integration/conftest.py``)
and writes there.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.documents import DocumentWriter
from app.domain.living_doc.services import Documents
from app.domain.project.services import ProjectService
from app.domain.room_task.services import TaskService
from app.domain.topic.doc_change import summarize_doc_change
from app.domain.topic.services import TopicService


async def write_doc(
    session: AsyncSession,
    task_id: uuid.UUID,
    content: str,
    actor: str = "alice",
    *,
    quiet: bool = False,
):
    """Record ``content`` as the task's next document version, by ``actor``.
    ``quiet``: the task is not told (no "编辑了文档" line)."""
    place = await TopicService(session).place_or_404(task_id)
    assert place.task is not None, "only a task has a living document"
    document_id = await TaskService(session).ensure_document(place.task)
    doc = await Documents(session).get(document_id)
    return await DocumentWriter(session, summarize_doc_change).record(
        doc, content=content, actors=[actor], quiet=quiet
    )


async def write_overview(
    session: AsyncSession,
    project_id: uuid.UUID,
    content: str,
    actor: str = "alice",
):
    """Record ``content`` as the project overview's next version, by ``actor``."""
    projects = ProjectService(session)
    doc = await projects.overview_document(await projects.get_or_404(project_id))
    return await DocumentWriter(session, summarize_doc_change).record(
        doc, content=content, actors=[actor]
    )


def document_of(client, task_id, **kwargs) -> str:
    """The id of the task's living document, as a page finds it out
    (``GET /topics/{id}/document``); ``kwargs`` go with the request (headers)."""
    response = client.get(f"/topics/{task_id}/document", **kwargs)
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def overview_of(client, project_id, **kwargs) -> str:
    """The id of the project's overview document, as a page finds it out
    (``GET /projects/{id}/overview``)."""
    response = client.get(f"/projects/{project_id}/overview", **kwargs)
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]
