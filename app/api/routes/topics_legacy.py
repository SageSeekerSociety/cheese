from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path

from app.auth.checker import get_auth_user
from app.auth.core import AuthUserInfo


router = APIRouter(prefix="/topics", tags=["Topics"])


@router.get(
    "",
    summary="List Topics",
)
async def list_topics(
    authorization: str | None = Header(default=None, alias="Authorization"),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    _ = (authorization, current_user_id)
    return {"code": 200, "message": "OK", "data": {"topics": []}}


@router.get(
    "/{topic_id}",
    summary="Get Topic",
)
async def get_topic(
    topic_id: Annotated[int, Path(ge=1)],
    authorization: str | None = Header(default=None, alias="Authorization"),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    _ = (authorization, current_user_id)
    topic = {
        "id": topic_id,
        "name": "",
    }
    return {"code": 200, "message": "OK", "data": {"topic": topic}}

