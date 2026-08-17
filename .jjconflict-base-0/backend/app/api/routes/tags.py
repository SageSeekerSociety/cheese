"""知是 tags (标签) — the labels a question, a 赛题 or a Space is filed under.

Served at `/topics` until #370. The word was owned twice: here it names a row of
{id, name, creator}, and in cheesex it names a room with a roster, a document, a
branch and 芝士. Losing one `/api` layer sent `/api/topics` to THIS router and it
answered 200 with tags — the failure docs/api-conventions.md was written about.

The JSON keys still say `topics`/`topic`: the 知是 UI calls these 话题 and that is
product language, changed by a product decision rather than by a rename. Only the
address and the code moved.
"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query

from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import BadRequestError, NotFoundError
from app.db.session import get_db
from app.domain.tag.repositories import TagRepository
from app.domain.tag.services import TagService

router = APIRouter(prefix="/tags", tags=["Tags"])


async def get_tag_service(db=Depends(get_db)) -> TagService:
    repo = TagRepository(session=db)
    return TagService(repo=repo)


@router.get(
    "",
    summary="List Tags",
)
async def list_tags(
    q: str | None = Query(default=None),
    page_start: int | None = Query(default=None, alias="page_start"),
    page_size: int = Query(default=50, ge=1, le=100, alias="page_size"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: TagService = Depends(get_tag_service),
) -> dict:
    if page_start is not None and page_start < 0:
        raise NotFoundError("Invalid page_start", data={"page_start": page_start})
    tags, page = await service.list_tags(
        keyword=q,
        page_start=page_start,
        page_size=page_size,
    )
    return {"code": 200, "message": "OK", "data": {"topics": tags, "page": page}}


@router.get(
    "/{tag_id}",
    summary="Get Tag",
)
async def get_tag(
    tag_id: Annotated[int, Path()],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: TagService = Depends(get_tag_service),
) -> dict:
    tag = await service.get_tag(tag_id)
    return {"code": 200, "message": "OK", "data": {"topic": tag}}


@router.post(
    "",
    summary="Create Tag",
    status_code=201,
)
async def create_tag(
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: TagService = Depends(get_tag_service),
) -> dict:
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise BadRequestError("name is required")
    result = await service.create_tag(name=name, created_by_id=auth_user.user_id)
    return {"code": 201, "message": "Created", "data": result}
