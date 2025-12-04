from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.auth.checker import get_auth_user
from app.auth.core import AuthUserInfo
from app.db.session import get_db
from app.domain.project.models import Project
from app.domain.project.repositories import ProjectRepository
from app.domain.project.services import ProjectService


router = APIRouter(prefix="/projects", tags=["Projects"])


async def get_project_service(db=Depends(get_db)) -> ProjectService:
    repo = ProjectRepository(session=db)
    return ProjectService(repo)


def _project_to_api_model(project: Project) -> dict:
    created_at_ms = (
        int(project.created_at.timestamp() * 1000) if project.created_at is not None else 0
    )
    updated_at_ms = (
        int(project.updated_at.timestamp() * 1000) if project.updated_at is not None else 0
    )
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "colorCode": project.color_code,
        "startDate": created_at_ms,
        "endDate": updated_at_ms,
        "parentId": None,
        "team": None,
        "leader": None,
        "externalTaskId": None,
        "githubRepo": None,
        "content": "",
        "createdAt": created_at_ms,
        "updatedAt": updated_at_ms,
    }


def _validate_color_code(value: str | None) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        raise BadRequestError("colorCode must match ^#[0-9A-Fa-f]{6}$")
    return value


@router.post(
    "",
    summary="Create Project",
    status_code=status.HTTP_201_CREATED,
)
async def create_project(
    payload: dict,
    service: ProjectService = Depends(get_project_service),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    _ = auth_user
    name = payload.get("name")
    description = payload.get("description") or ""
    color_code = _validate_color_code(payload.get("colorCode"))
    if not isinstance(name, str) or not name.strip():
        raise BadRequestError("name is required")

    project = await service.create_project(
        name=name.strip(),
        description=str(description),
        color_code=color_code,
    )
    return {
        "code": 201,
        "message": "Created",
        "data": {"project": _project_to_api_model(project)},
    }


@router.get(
    "/{projectId}",
    summary="Query Project",
)
async def get_project(
    project_id: Annotated[int, Path(ge=1, alias="projectId")],
    service: ProjectService = Depends(get_project_service),
) -> dict:
    project = await service.get_project(project_id=project_id)
    if project is None:
        raise NotFoundError("Project not found")

    return {
        "code": 200,
        "message": "success",
        "data": {"project": _project_to_api_model(project)},
    }


@router.patch(
    "/{projectId}",
    summary="Update Project",
)
async def patch_project(
    project_id: Annotated[int, Path(ge=1, alias="projectId")],
    payload: dict,
    service: ProjectService = Depends(get_project_service),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    _ = auth_user
    project = await service.get_project(project_id=project_id)
    if project is None:
        raise NotFoundError("Project not found")

    name = payload.get("name")
    description = payload.get("description")
    color_code = payload.get("colorCode")
    archived = payload.get("archived")

    if color_code is not None:
        color_code = _validate_color_code(color_code)
    if name is not None and (not isinstance(name, str) or not name.strip()):
        raise BadRequestError("name must be string")
    if description is not None and not isinstance(description, str):
        raise BadRequestError("description must be string")
    if archived is not None and not isinstance(archived, bool):
        raise BadRequestError("archived must be boolean")

    updated = await service.update_project(
        project,
        name=name.strip() if isinstance(name, str) and name.strip() else None,
        description=str(description) if isinstance(description, str) else None,
        color_code=color_code,
        archived=archived,
    )
    return {
        "code": 200,
        "message": "success",
        "data": {"project": _project_to_api_model(updated)},
    }


@router.delete(
    "/{projectId}",
    summary="Delete Project",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_project(
    project_id: Annotated[int, Path(ge=1, alias="projectId")],
    service: ProjectService = Depends(get_project_service),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> None:
    _ = auth_user
    project = await service.get_project(project_id=project_id)
    if project is None:
        raise NotFoundError("Project not found")
    await service.soft_delete_project(project)


@router.get(
    "",
    summary="List Projects",
)
async def get_projects(
    team_id: int = Query(..., description="Team ID"),
    parent_id: int | None = Query(default=None),
    leader_id: int | None = Query(default=None),
    member_id: int | None = Query(default=None),
    archived: bool | None = Query(default=None),
    service: ProjectService = Depends(get_project_service),
) -> dict:
    projects = await service.list_projects(
        team_id=team_id,
        parent_id=parent_id,
        leader_id=leader_id,
        member_id=member_id,
        archived=archived,
    )
    items = [_project_to_api_model(p) for p in projects]
    return {
        "code": 200,
        "message": "success",
        "data": {"projects": items},
    }


@router.get(
    "/{projectId}/members",
    summary="Enumerate Project Members",
)
async def get_project_members(
    project_id: Annotated[int, Path(ge=1, alias="projectId")],
    page_start: str | None = Query(default=None, alias="pageStart"),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    _ = (project_id, current_user_id)
    members: list[dict] = []
    page = {
        "pageStart": page_start or "",
        "pageSize": min(page_size, len(members)),
        "hasMore": False,
        "nextStart": None,
        "total": len(members),
    }
    return {
        "code": 200,
        "message": "success",
        "data": {"members": members, "page": page},
    }


def _build_member_stub(user_id: int, role: str, notes: str | None = None) -> dict:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    return {
        "userId": user_id,
        "role": role,
        "notes": notes or "",
        "createdAt": now_ms,
        "updatedAt": now_ms,
    }


@router.post(
    "/{projectId}/members",
    summary="Add Project Member",
    status_code=status.HTTP_201_CREATED,
)
async def add_project_member(
    project_id: Annotated[int, Path(ge=1, alias="projectId")],
    payload: dict,
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    _ = (project_id, current_user_id)
    user_id = payload.get("userId")
    role = payload.get("role") or "MEMBER"
    notes = payload.get("notes")
    if not isinstance(user_id, int) or user_id <= 0:
        raise BadRequestError("userId must be positive")
    if not isinstance(role, str):
        raise BadRequestError("role must be string")

    member = _build_member_stub(
        user_id=user_id,
        role=role.upper(),
        notes=notes if isinstance(notes, str) else None,
    )
    return {
        "code": 201,
        "message": "Member added",
        "data": {"member": member},
    }


@router.delete(
    "/{projectId}/members/{userId}",
    summary="Remove Project Member",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_project_member(
    project_id: Annotated[int, Path(ge=1, alias="projectId")],
    user_id: Annotated[int, Path(ge=1, alias="userId")],
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> None:
    _ = (project_id, user_id, current_user_id)
    return None
