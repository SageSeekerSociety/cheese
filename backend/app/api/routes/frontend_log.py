"""Frontend error reports → run records (see app.domain.frontend_log)."""

from fastapi import APIRouter

from app.api.request_deps import DbSession
from app.api.response import ok
from app.domain import frontend_log  # module import: tests swap the intake singleton
from app.domain.frontend_log import FrontendErrorBatchIn

router = APIRouter(prefix="/frontend-errors", tags=["frontend-errors"])


@router.post("")
async def report_frontend_errors(body: FrontendErrorBatchIn, db: DbSession) -> dict:
    """Keep browser-side errors for the admin page. Dedup and rate-limiting
    happen in the intake — a dropped duplicate still returns 200, so the
    reporting client never retries."""
    return ok(await frontend_log.record(db, body))
