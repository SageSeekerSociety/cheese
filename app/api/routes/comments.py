from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, Path, Query

from app.auth.checker import get_auth_user
from app.auth.core import AuthUserInfo


router = APIRouter(prefix="/comments", tags=["Comments"])


@router.get(
    "/{commentableType}/{commentableId}",
    summary="Get Comments",
)
async def get_comments(
    commentableType: str,
    commentableId: Annotated[int, Path(ge=1)],
    page_start: int | None = Query(default=None),
    page_size: int = Query(default=20, ge=1, le=100),
    authorization: str | None = Header(default=None, alias="Authorization"),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    _ = (authorization, current_user_id, commentableType, commentableId)
    comments: list[dict] = []
    page = {
        "pageStart": page_start or 0,
        "pageSize": page_size,
        "hasMore": False,
        "nextStart": None,
        "total": 0,
    }
    return {"code": 200, "message": "Get comments successfully", "data": {"comments": comments, "page": page}}


@router.post(
    "/{commentId}/attitudes",
    summary="Update Attitude To Comment",
)
async def update_attitude_to_comment(
    commentId: Annotated[int, Path(ge=1)],
    payload: dict = Body(...),
    authorization: str | None = Header(default=None, alias="Authorization"),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    _ = (payload, authorization, current_user_id, commentId)
    return {
        "code": 200,
        "message": "You have expressed your attitude towards the comment",
        "data": {"attitudes": []},
    }


@router.post(
    "/{commentableType}/{commentableId}",
    summary="Create Comment",
)
async def create_comment(
    commentableType: str,
    commentableId: Annotated[int, Path(ge=1)],
    content: str = Body(..., embed=True),
    authorization: str | None = Header(default=None, alias="Authorization"),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    _ = (authorization, current_user_id, commentableType, commentableId, content)
    comment_id = 1
    return {
        "code": 201,
        "message": "Comment created successfully",
        "data": {"id": comment_id},
    }
