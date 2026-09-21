from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.db.session import get_db
from app.domain.space.review_service import SpaceReviewService

router = APIRouter(prefix="/space-applications", tags=["Spaces"])


class ResubmitSpaceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    intro: str = ""
    avatar_id: int | None = Field(default=None, alias="avatarId")


@router.get("")
async def list_my_applications(
    status: Literal["PENDING", "REJECTED", "APPROVED"] | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    items = await SpaceReviewService(db).list_applications(
        owner_id=user.user_id, status=status, offset=offset, limit=limit
    )
    return {"code": 200, "data": {"items": items}}


@router.post("/{space_id}/resubmit")
async def resubmit_space(
    space_id: int,
    body: ResubmitSpaceRequest,
    user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    item = await SpaceReviewService(db).resubmit(
        space_id,
        user_id=user.user_id,
        name=body.name,
        intro=body.intro,
        avatar_id=body.avatar_id,
    )
    return {"code": 200, "data": {"application": item}}
