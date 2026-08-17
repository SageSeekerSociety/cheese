"""Backend error reports → 现场 (see app.domain.backend_log).

The receiving end of the push channel. A process that raised an unhandled
exception POSTs it here and it becomes an event block in the room — the same
timeline the browser reporter already writes to, so a backend 500 and the
frontend error it caused lie side by side.

Authenticated, unlike `/api/frontend-errors`: a browser cannot hold a secret,
but a reporting backend runs in a container that already carries one. A scoped
`X-Cheese-Token` also *names* the room, so the reporter never has to know (or be
trusted about) which project it is writing into. The path carries no project or
topic id, so `cheese_token_gate` cannot scope this route — it does its own check,
like the /sandbox endpoints.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok
from app.core.db import get_db
from app.core.errors import BadRequestError, NotFoundError, UnauthorizedError
from app.core.sandbox_auth import is_global_sandbox_token, scoped_token_claims
from app.domain import backend_log  # module import: tests swap the intake singleton
from app.domain.backend_log import BackendErrorBatchIn

router = APIRouter(prefix="/backend-errors", tags=["backend-errors"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


def _room(
    token: str, body: BackendErrorBatchIn
) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """Which room the reports belong to. A scoped token's claims WIN over the
    body — a container reporting for project A must not be able to write into
    project B by asserting a different id."""
    claims = scoped_token_claims(token)
    if claims is not None:
        project = claims.get("p")
        topic = claims.get("t")
        return (
            uuid.UUID(project) if project else None,
            uuid.UUID(topic) if topic else None,
        )
    if is_global_sandbox_token(token):
        # The signing secret itself is project-less, so the body has to say.
        if body.project_id is None and body.topic_id is None:
            raise BadRequestError("project_id or topic_id required with a global token")
        return body.project_id, body.topic_id
    raise UnauthorizedError("invalid sandbox token")


@router.post("")
async def report_backend_errors(
    body: BackendErrorBatchIn,
    db: DbSession,
    x_cheese_token: Annotated[str, Header(alias="X-Cheese-Token")] = "",
) -> dict:
    """Persist unhandled backend failures as 现场 event blocks so agents (who can
    reach neither `docker logs` nor the host's log file) and humans debug from
    the same timeline. Dedup, burst-summarization and the hourly cap happen in
    the intake — a silently dropped report still returns 200, so a reporter in a
    crash loop never retries and never amplifies."""
    project_id, topic_id = _room(x_cheese_token, body)
    result = await backend_log.record(
        db, project_id=project_id, topic_id=topic_id, errors=body.errors
    )
    if result is None:
        raise NotFoundError("No topic to attach backend errors to")
    return ok(result)
