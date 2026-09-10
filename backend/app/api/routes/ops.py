"""操作卡 routes — the registry and the requests sitting on a topic's branch.

Read-only on purpose (第 3 步：只登记不执行). Adding a POST that runs something
here is the exact mistake this step is scoped to avoid.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok, page
from app.api.routes.workspace import require_project_access
from app.core.db import get_db
from app.domain.ops.registry import describe_registry
from app.domain.ops.services import OperationRequestService
from app.domain.project.services import ProjectService

router = APIRouter(prefix="", tags=["ops"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.get("/ops/registry")
async def ops_registry() -> dict:
    """What operations exist, and the arg schema each one takes."""
    operations = describe_registry()
    return ok(page(operations, len(operations)))


@router.get(
    "/projects/{project_id}/operation-requests",
    dependencies=[Depends(require_project_access)],
)
async def list_operation_requests(
    project_id: uuid.UUID,
    db: DbSession,
    topic: uuid.UUID | None = None,
    task: uuid.UUID | None = None,
) -> dict:
    """The operation requests on this topic's branch, as card faces."""
    await ProjectService(db).get_or_404(project_id)
    requests = OperationRequestService(project_id, topic_id=task).list_requests()
    items = [r.model_dump(mode="json") for r in requests]
    return ok(page(items, len(items)))
