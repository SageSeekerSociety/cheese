from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query

from app.auth.checker import get_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import BadRequestError
from app.db.session import get_db
from app.domain.discussion.repositories import (
    DiscussionRepository,
    DiscussionReactionRepository,
    ReactionTypeRepository,
)
from app.domain.discussion.reaction_services import DiscussionReactionService
from app.domain.discussion.services import DiscussionService
from app.domain.user.repositories import UserProfileRepository

router = APIRouter(prefix="/discussions", tags=["Discussions"])


def build_discussion_service(db) -> DiscussionService:
    repo = DiscussionRepository(session=db)
    reaction_repo = DiscussionReactionRepository(session=db)
    reaction_type_repo = ReactionTypeRepository(session=db)
    reaction_service = DiscussionReactionService(reaction_repo, reaction_type_repo)
    profile_repo = UserProfileRepository(session=db)
    return DiscussionService(
        repo=repo,
        reaction_service=reaction_service,
        profile_repo=profile_repo,
        session=db,
    )


async def get_discussion_service(db=Depends(get_db)) -> DiscussionService:
    return build_discussion_service(db)


@router.post("", summary="Create Discussion")
async def create_discussion(
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: DiscussionService = Depends(get_discussion_service),
) -> dict:
    model_type = payload.get("modelType")
    model_id = payload.get("modelId")
    content = payload.get("content")
    parent_id = payload.get("parentId")
    mentioned = payload.get("mentionedUserIds") or []
    if not isinstance(model_type, str) or not model_type.strip():
        raise BadRequestError("modelType is required")
    if not isinstance(model_id, int) or model_id <= 0:
        raise BadRequestError("modelId must be a positive integer")
    if parent_id is not None and (not isinstance(parent_id, int) or parent_id <= 0):
        raise BadRequestError("parentId must be positive when provided")
    mentioned_ids = [int(x) for x in mentioned if isinstance(x, int) and x > 0]

    discussion = await service.create_discussion(
        user_id=auth_user.user_id,
        content=str(content or ""),
        model_type=model_type,
        model_id=model_id,
        parent_id=parent_id,
        mentioned_user_ids=mentioned_ids,
    )
    return {"code": 200, "message": "OK", "data": {"discussion": discussion}}


@router.get("", summary="List Discussions")
async def list_discussions(
    modelType: str | None = Query(default=None),
    modelId: int | None = Query(default=None),
    parentId: int | None = Query(default=None),
    pageStart: int | None = Query(default=None),
    pageSize: int = Query(default=20, ge=1, le=100),
    sortBy: str = Query(default="createdAt"),
    sortOrder: str = Query(default="desc"),
    withReactions: bool = Query(default=True),
    withSubDiscussions: bool = Query(default=True),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: DiscussionService = Depends(get_discussion_service),
) -> dict:
    if sortBy not in {"createdAt", "updatedAt"}:
        raise BadRequestError(f"Invalid sortBy: {sortBy}")
    if sortOrder.lower() not in {"asc", "desc"}:
        raise BadRequestError(f"Invalid sortOrder: {sortOrder}")
    if modelId is not None and modelId <= 0:
        raise BadRequestError("modelId must be positive if provided")
    if parentId is not None and parentId <= 0:
        raise BadRequestError("parentId must be positive if provided")

    rows, page = await service.list_discussions(
        model_type=modelType.upper() if isinstance(modelType, str) else None,
        model_id=modelId,
        parent_id=parentId,
        page_start=pageStart,
        page_size=pageSize,
        sort_by=sortBy,
        sort_order=sortOrder,
        current_user_id=auth_user.user_id,
        include_subs=withSubDiscussions,
        with_reactions=withReactions,
    )
    return {"code": 200, "message": "OK", "data": {"discussions": rows, "page": page}}


@router.get("/{discussionId}", summary="Get Discussion")
async def get_discussion(
    discussion_id: Annotated[int, Path(ge=1, alias="discussionId")],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: DiscussionService = Depends(get_discussion_service),
) -> dict:
    discussion = await service.get_discussion(discussion_id, current_user_id)
    return {"code": 200, "message": "OK", "data": {"discussion": discussion}}


@router.patch("/{discussionId}", summary="Update Discussion")
async def patch_discussion(
    discussion_id: Annotated[int, Path(ge=1, alias="discussionId")],
    payload: dict,
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: DiscussionService = Depends(get_discussion_service),
) -> dict:
    _ = payload
    discussion = await service.get_discussion(discussion_id, current_user_id)
    return {"code": 200, "message": "OK", "data": {"discussion": discussion}}


@router.get("/{discussionId}/sub-discussions", summary="List Sub Discussions")
async def list_sub_discussions(
    discussion_id: Annotated[int, Path(ge=1, alias="discussionId")],
    pageStart: int | None = Query(default=None),
    pageSize: int = Query(default=20, ge=1, le=100),
    sortBy: str = Query(default="createdAt"),
    sortOrder: str = Query(default="desc"),
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: DiscussionService = Depends(get_discussion_service),
) -> dict:
    if sortBy not in {"createdAt", "updatedAt"}:
        raise BadRequestError(f"Invalid sortBy: {sortBy}")
    if sortOrder.lower() not in {"asc", "desc"}:
        raise BadRequestError(f"Invalid sortOrder: {sortOrder}")
    rows, page = await service.list_discussions(
        model_type=None,
        model_id=None,
        parent_id=discussion_id,
        page_start=pageStart,
        page_size=pageSize,
        sort_by=sortBy,
        sort_order=sortOrder,
        current_user_id=auth_user.user_id,
        include_subs=False,
        with_reactions=True,
    )
    return {"code": 200, "message": "OK", "data": {"discussions": rows, "page": page}}


@router.delete("/{discussionId}", summary="Delete Discussion")
async def delete_discussion(
    discussion_id: Annotated[int, Path(ge=1, alias="discussionId")],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: DiscussionService = Depends(get_discussion_service),
) -> dict:
    _ = auth_user
    await service.delete_discussion(discussion_id)
    return {"code": 200, "message": "OK"}


@router.post(
    "/{discussionId}/reactions/{reactionTypeId}",
    summary="Toggle Discussion Reaction",
)
async def toggle_reaction(
    discussion_id: Annotated[int, Path(ge=1, alias="discussionId")],
    reaction_type_id: Annotated[int, Path(ge=1, alias="reactionTypeId")],
    auth_user: AuthUserInfo = Depends(get_auth_user),
    service: DiscussionService = Depends(get_discussion_service),
) -> dict:
    result = await service.toggle_reaction(
        discussion_id=discussion_id,
        reaction_type_id=reaction_type_id,
        user_id=auth_user.user_id,
    )
    return {"code": 200, "message": "OK", "data": result}


@router.get(
    "/reactions",
    summary="Get all reaction types",
)
async def list_reaction_types(
    service: DiscussionService = Depends(get_discussion_service),
) -> dict:
    types = await service.list_reaction_types()
    return {"code": 200, "message": "OK", "data": {"reactionTypes": types}}
