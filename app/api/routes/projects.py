from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from app.auth.checker import get_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import BadRequestError, NotFoundError
from app.db.session import get_db
from app.domain.project.models import Project, ProjectMemberRole, ProjectMembership
from app.domain.project.repositories import ProjectMembershipRepository, ProjectRepository
from app.domain.project.services import ProjectService


router = APIRouter(prefix="/projects", tags=["Projects"])


async def get_project_service(db=Depends(get_db)) -> ProjectService:
    repo = ProjectRepository(session=db)
    membership_repo = ProjectMembershipRepository(session=db)
    return ProjectService(repo, membership_repo)


def _project_to_api_model(project: Project) -> dict:
    created_at_ms = (
        int(project.created_at.timestamp() * 1000) if project.created_at is not None else 0
    )
    updated_at_ms = (
        int(project.updated_at.timestamp() * 1000) if project.updated_at is not None else 0
    )
    start_date_ms = (
        int(project.start_date.timestamp() * 1000) if project.start_date is not None else 0
    )
    end_date_ms = int(project.end_date.timestamp() * 1000) if project.end_date is not None else 0
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "colorCode": project.color_code,
        "content": project.content or "",
        "startDate": start_date_ms,
        "endDate": end_date_ms,
        "teamId": project.team_id,
        "leaderId": project.leader_id,
        "parentId": project.parent_id,
        "externalTaskId": project.external_task_id,
        "githubRepo": project.github_repo,
        "team": None,
        "leader": None,
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
    status_code=status.HTTP_200_OK,
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
    team_id = payload.get("teamId")
    leader_id = payload.get("leaderId")
    start_date = payload.get("startDate")
    end_date = payload.get("endDate")
    content = payload.get("content")
    parent_id = payload.get("parentId")
    external_task_id = payload.get("externalTaskId")
    github_repo = payload.get("githubRepo")

    if not isinstance(name, str) or not name.strip():
        raise BadRequestError("name is required")
    if not isinstance(team_id, int) or team_id <= 0:
        raise BadRequestError("teamId is required")
    if not isinstance(leader_id, int) or leader_id <= 0:
        raise BadRequestError("leaderId is required")
    if not isinstance(start_date, int):
        raise BadRequestError("startDate is required")
    if not isinstance(end_date, int):
        raise BadRequestError("endDate is required")

    project = await service.create_project(
        name=name.strip(),
        description=str(description),
        color_code=color_code,
        team_id=team_id,
        leader_id=leader_id,
        start_date=start_date,
        end_date=end_date,
        content=content if isinstance(content, str) else None,
        parent_id=parent_id if isinstance(parent_id, int) else None,
        external_task_id=external_task_id if isinstance(external_task_id, int) else None,
        github_repo=github_repo if isinstance(github_repo, str) else None,
    )
    return {
        "code": 200,
        "message": "OK",
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


async def _get_project_or_404(service: ProjectService, project_id: int) -> None:
    project = await service.get_project(project_id)
    if project is None:
        raise NotFoundError("Project not found")


def _membership_to_api_model(m: ProjectMembership) -> dict:
    role_names = {
        ProjectMemberRole.MEMBER.value: "MEMBER",
        ProjectMemberRole.ADMIN.value: "ADMIN",
        ProjectMemberRole.OWNER.value: "OWNER",
    }
    return {
        "userId": m.user_id,
        "role": role_names.get(m.role, "MEMBER"),
        "notes": m.notes or "",
        "createdAt": int(m.created_at.timestamp() * 1000) if m.created_at else 0,
        "updatedAt": int(m.updated_at.timestamp() * 1000) if m.updated_at else 0,
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
    service: ProjectService = Depends(get_project_service),
) -> dict:
    _ = auth_user
    await _get_project_or_404(service, project_id)
    offset = int(page_start) if page_start and page_start.isdigit() else 0
    members, total = await service.list_members(project_id, limit=page_size, offset=offset)
    next_offset = offset + len(members)
    has_more = next_offset < total
    return {
        "code": 200,
        "message": "success",
        "data": {
            "members": [_membership_to_api_model(m) for m in members],
            "page": {
                "pageStart": page_start or "0",
                "pageSize": len(members),
                "hasMore": has_more,
                "nextStart": str(next_offset) if has_more else None,
                "total": total,
            },
        },
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
    service: ProjectService = Depends(get_project_service),
) -> dict:
    _ = auth_user
    await _get_project_or_404(service, project_id)
    user_id = payload.get("userId")
    role = payload.get("role") or "MEMBER"
    notes = payload.get("notes")
    if not isinstance(user_id, int) or user_id <= 0:
        raise BadRequestError("userId must be positive")
    if not isinstance(role, str):
        raise BadRequestError("role must be string")

    membership = await service.add_member(
        project_id=project_id,
        user_id=user_id,
        role=role.upper(),
        notes=notes if isinstance(notes, str) else "",
    )
    return {
        "code": 201,
        "message": "Member added",
        "data": {"member": _membership_to_api_model(membership)},
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
    service: ProjectService = Depends(get_project_service),
) -> None:
    _ = auth_user
    await _get_project_or_404(service, project_id)
    await service.remove_member(project_id=project_id, user_id=user_id)
