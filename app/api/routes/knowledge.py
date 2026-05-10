# ── Request Models ────────────────────────────────────────────────────────────
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Path, Query
from pydantic import BaseModel, ConfigDict

from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import BadRequestError
from app.db.session import get_db
from app.domain.knowledge.repositories import KnowledgeRepository
from app.domain.knowledge.services import KnowledgeService
from app.domain.team.repositories import TeamRepository
from app.domain.user.repositories import UserProfileRepository, UserRepository


class PatchKnowledgeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    description: str | None = None
    content: str | dict[str, Any] | list | None = None
    labels: list[str] | None = None


router = APIRouter(prefix="/knowledge", tags=["Knowledge"])


async def get_knowledge_service(db=Depends(get_db)) -> KnowledgeService:
    repo = KnowledgeRepository(session=db)
    team_repo = TeamRepository(session=db)
    user_repo = UserRepository(session=db)
    profile_repo = UserProfileRepository(session=db)
    return KnowledgeService(
        repo=repo,
        team_repo=team_repo,
        user_repo=user_repo,
        profile_repo=profile_repo,
    )


@router.post(
    "",
    summary="Create Knowledge",
    status_code=201,
)
async def create_knowledge(
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> dict:
    name = payload.get("name")
    type_raw = payload.get("type", "TEXT")
    content = payload.get("content")
    description = payload.get("description")
    team_id = payload.get("teamId")
    if not isinstance(team_id, int) or team_id <= 0:
        raise BadRequestError("teamId must be a positive integer")
    if not isinstance(name, str) or not name.strip():
        raise BadRequestError("name is required")
    # Frontend's CreateKnowledgeRequest types content as `string` (and reads
    # it back via JSON.parse), matching NT KnowledgeEntity.content: String?.
    # Allow string, dict, or list — JSONB stores any JSON value verbatim.
    if content is None:
        content = ""
    if not isinstance(content, (str, dict, list)):
        raise BadRequestError("content must be a string, object, or array")
    type_str = str(type_raw).upper()
    if type_str not in {"MATERIAL", "LINK", "TEXT", "CODE"}:
        raise BadRequestError(f"Invalid knowledge type: {type_raw}")

    material_id = payload.get("materialId")
    project_id = payload.get("projectId")
    discussion_id = payload.get("discussionId")
    labels_raw = payload.get("labels") or []
    labels: list[str] = [str(item) for item in labels_raw if isinstance(item, str) and item.strip()]

    knowledge = await service.create(
        name=name,
        type_=type_str,
        content=content,
        description=description,
        team_id=team_id,
        created_by=auth_user.user_id,
        labels=labels,
        material_id=material_id,
        project_id=project_id,
        discussion_id=discussion_id,
    )
    return {
        "code": 201,
        "message": "Created",
        "data": {"knowledge": knowledge},
    }


@router.get(
    "",
    summary="List Knowledge",
)
async def list_knowledge(
    teamId: int = Query(..., description="Team ID"),
    projectId: int | None = Query(default=None),
    type: str | None = Query(default=None),
    labels: list[str] | None = Query(default=None),
    query: str | None = Query(default=None),
    pageStart: int | None = Query(default=None),
    pageSize: int = Query(default=20, ge=1, le=200),
    sortBy: str = Query(default="createdAt"),
    sortOrder: str = Query(default="desc"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> dict:
    _ = auth_user
    type_str: str | None = type.upper() if isinstance(type, str) else None
    if type_str is not None and type_str not in {"MATERIAL", "LINK", "TEXT", "CODE"}:
        raise BadRequestError(f"Invalid type: {type}")
    if sortBy not in {"createdAt", "updatedAt"}:
        raise BadRequestError(f"Invalid sortBy: {sortBy}")
    if sortOrder.lower() not in {"asc", "desc"}:
        raise BadRequestError(f"Invalid sortOrder: {sortOrder}")

    offset = pageStart or 0
    if offset < 0:
        offset = 0

    rows, total = await service.find_all(
        team_id=teamId,
        user_id=auth_user.user_id,
        project_id=projectId,
        type_=type_str,
        labels=labels,
        query=query,
        limit=pageSize,
        offset=offset,
        sort_by=sortBy,
        sort_order=sortOrder,
    )
    items = rows
    returned = len(items)
    has_more = offset + returned < total
    next_start = offset + returned if has_more and returned > 0 else None
    page = {
        "pageStart": offset,
        "pageSize": returned,
        "hasMore": has_more,
        "nextStart": next_start,
        "total": total,
    }
    data = {"knowledges": items, "page": page}
    return {"code": 200, "message": "success", "data": data}


@router.get(
    "/{knowledgeId}",
    summary="Get Knowledge By Id",
)
async def get_knowledge_by_id(
    knowledge_id: Annotated[int, Path(ge=1, alias="knowledgeId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> dict:
    knowledge = await service.get(knowledge_id=knowledge_id, user_id=auth_user.user_id)
    return {
        "code": 200,
        "message": "success",
        "data": {"knowledge": knowledge},
    }


@router.patch(
    "/{knowledgeId}",
    summary="Patch Knowledge",
)
async def patch_knowledge(
    knowledge_id: Annotated[int, Path(ge=1, alias="knowledgeId")],
    payload: PatchKnowledgeRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> dict:
    knowledge = await service.update(
        knowledge_id=knowledge_id,
        user_id=auth_user.user_id,
        name=payload.name,
        description=payload.description,
        content=payload.content,
        labels=payload.labels,
    )
    return {
        "code": 200,
        "message": "success",
        "data": {"knowledge": knowledge},
    }


@router.delete(
    "/{knowledgeId}",
    summary="Delete Knowledge",
)
async def delete_knowledge(
    knowledge_id: Annotated[int, Path(ge=1, alias="knowledgeId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> dict:
    await service.delete(knowledge_id=knowledge_id, user_id=auth_user.user_id)
    return {
        "code": 200,
        "message": "OK",
    }


@router.post(
    "/{knowledgeId}/upvote",
    summary="Upvote Knowledge Item",
)
async def upvote_knowledge(
    knowledge_id: Annotated[int, Path(ge=1, alias="knowledgeId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> dict:
    try:
        knowledge = await service.upvote(knowledge_id=knowledge_id, user_id=auth_user.user_id)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    return {
        "code": 200,
        "message": "success",
        "data": {"knowledge": knowledge},
    }


@router.delete(
    "/{knowledgeId}/upvote",
    summary="Remove Knowledge Upvote",
)
async def remove_upvote_knowledge(
    knowledge_id: Annotated[int, Path(ge=1, alias="knowledgeId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> dict:
    knowledge = await service.remove_upvote(knowledge_id=knowledge_id, user_id=auth_user.user_id)
    return {
        "code": 200,
        "message": "success",
        "data": {"knowledge": knowledge},
    }
