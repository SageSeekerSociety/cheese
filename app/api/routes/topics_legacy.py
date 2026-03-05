from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query

from app.auth.checker import get_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import BadRequestError, NotFoundError
from app.db.session import get_db
from app.domain.topics.repositories import TopicRepository
from app.domain.topics.services import TopicService

router = APIRouter(prefix="/topics", tags=["Topics"])


async def get_topic_service(db=Depends(get_db)) -> TopicService:
    repo = TopicRepository(session=db)
    return TopicService(repo=repo)


@router.get(
    "",
    summary="List Topics",
)
async def list_topics(
    q: str | None = Query(default=None),
    page_start: int | None = Query(default=None, alias="page_start"),
    page_size: int = Query(default=50, ge=1, le=100, alias="page_size"),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: TopicService = Depends(get_topic_service),
) -> dict:
    if page_start is not None and page_start < 0:
        raise NotFoundError("Invalid page_start", data={"page_start": page_start})
    topics, page = await service.list_topics(
        keyword=q,
        page_start=page_start,
        page_size=page_size,
    )
    return {"code": 200, "message": "OK", "data": {"topics": topics, "page": page}}


@router.get(
    "/{topic_id}",
    summary="Get Topic",
)
async def get_topic(
    topic_id: Annotated[int, Path()],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: TopicService = Depends(get_topic_service),
) -> dict:
    topic = await service.get_topic(topic_id)
    return {"code": 200, "message": "OK", "data": {"topic": topic}}


@router.post(
    "",
    summary="Create Topic",
    status_code=201,
)
async def create_topic(
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: TopicService = Depends(get_topic_service),
) -> dict:
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise BadRequestError("name is required")
    result = await service.create_topic(name=name, created_by_id=auth_user.user_id)
    return {"code": 201, "message": "Created", "data": result}
