from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.api.routes.admin_common import DbSession, PlatformAdminDep
from app.domain.space.review_service import SpaceReviewService

router = APIRouter(prefix="/admin/spaces", tags=["admin"])


class ReviewSpaceRequest(BaseModel):
    approved: bool
    reason: str = ""


@router.get("")
async def list_space_reviews(
    db: DbSession,
    handle: PlatformAdminDep,
    status: Literal["PENDING", "APPROVED", "REJECTED"] = "PENDING",
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> dict:
    items = await SpaceReviewService(db).list_applications(
        status=status, offset=offset, limit=limit
    )
    return {"code": 200, "data": {"items": items}}


@router.post("/{space_id}/review")
async def review_space(
    space_id: int, body: ReviewSpaceRequest, db: DbSession, handle: PlatformAdminDep
) -> dict:
    item = await SpaceReviewService(db).review(
        space_id, approved=body.approved, reason=body.reason, reviewer=handle
    )
    return {"code": 200, "data": {"application": item}}
