from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.admin_deps import PlatformAdminDep
from app.api.request_deps import DbSession
from app.api.response import ok
from app.api.write_access import ROUTE_DECIDES
from app.domain.space.review_service import SpaceReviewService

router = APIRouter(prefix="/admin/spaces", tags=["admin"], dependencies=[ROUTE_DECIDES])


class ReviewSpaceRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    approved: bool
    reason: str = ""
    #: For a request to open a space: the scope the reviewer was shown. The
    #: owner can rewrite a waiting request, so a decision on a scope other
    #: than the one it now holds is refused (409).
    open_scope: list[str] | None = Field(default=None, alias="openScope")


@router.get("")
async def list_space_reviews(
    db: DbSession,
    handle: PlatformAdminDep,
    status: Literal["PENDING", "APPROVED", "REJECTED"] = "PENDING",
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> dict:
    items = await SpaceReviewService(db).list_reviews(
        status=status, offset=offset, limit=limit
    )
    return ok({"items": items})


@router.post("/{space_id}/review")
async def review_space(
    space_id: int, body: ReviewSpaceRequest, db: DbSession, handle: PlatformAdminDep
) -> dict:
    item = await SpaceReviewService(db).review(
        space_id,
        approved=body.approved,
        reason=body.reason,
        reviewer=handle,
        shown_scope=body.open_scope,
    )
    # Commit before answering. ``get_db`` commits in its teardown, which FastAPI
    # runs after the response has gone out, so a client told "approved" could
    # publish into the space on its next request and still find it PENDING
    # ("Space must be approved before creating tasks"). Same reason as the
    # commit in ``create_space``.
    await db.commit()
    return ok({"application": item})
