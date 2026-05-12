import re
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.db.session import get_db
from app.domain.project.models import Project, ProjectMemberRole, ProjectMembership
from app.domain.project.repositories import ProjectMembershipRepository, ProjectRepository
from app.domain.project.services import ProjectService
from app.domain.team.models import Team
from app.domain.team.repositories import TeamRepository
from app.domain.user.models import User, UserProfile
from app.domain.user.repositories import UserProfileRepository, UserRepository

# ── Request Models ────────────────────────────────────────────────────────────


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., min_length=1)
    description: str = ""
    color_code: str | None = Field(default=None, alias="colorCode")
    team_id: int = Field(..., alias="teamId", gt=0)
    leader_id: int = Field(..., alias="leaderId", gt=0)
    start_date: int = Field(..., alias="startDate")
    end_date: int = Field(..., alias="endDate")
    content: str | None = None
    parent_id: int | None = Field(default=None, alias="parentId")
    external_task_id: int | None = Field(default=None, alias="externalTaskId")
    github_repo: str | None = Field(default=None, alias="githubRepo")


class PatchProjectRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    description: str | None = None
    color_code: str | None = Field(default=None, alias="colorCode")
    archived: bool | None = None


class AddProjectMemberRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: int = Field(..., alias="userId", gt=0)
    role: str = "MEMBER"
    notes: str | None = None


router = APIRouter(prefix="/projects", tags=["Projects"])


async def get_project_service(db=Depends(get_db)) -> ProjectService:
    repo = ProjectRepository(session=db)
    membership_repo = ProjectMembershipRepository(session=db)
    return ProjectService(repo, membership_repo)


# Frontend's ProjectMemberRole = 'LEADER' | 'MEMBER' | 'EXTERNAL'.
# Python's ProjectMemberRole = MEMBER (0) | ADMIN (1) | OWNER (2).
# OWNER is the project lead; ADMIN has no frontend counterpart so we surface
# it as MEMBER (frontend role-color logic only special-cases LEADER).
_PROJECT_ROLE_TO_FRONTEND = {
    ProjectMemberRole.MEMBER.value: "MEMBER",
    ProjectMemberRole.ADMIN.value: "MEMBER",
    ProjectMemberRole.OWNER.value: "LEADER",
}


def _team_summary(team: Team | None) -> dict | None:
    if team is None:
        return None
    return {
        "id": team.id,
        "name": team.name,
        "intro": team.intro or "",
        "avatarId": team.avatar_id,
    }


def _user_summary(user: User | None, profile: UserProfile | None) -> dict | None:
    if user is None:
        return None
    return {
        "id": user.id,
        "username": user.username,
        "nickname": profile.nickname if profile else user.username,
        "avatarId": profile.avatar_id if profile else None,
        "intro": profile.intro if profile else "",
    }


async def _project_to_api_model(project: Project, *, db: AsyncSession) -> dict:
    team_repo = TeamRepository(session=db)
    user_repo = UserRepository(session=db)
    profile_repo = UserProfileRepository(session=db)
    membership_repo = ProjectMembershipRepository(session=db)

    team = await team_repo.get_by_id(project.team_id)
    leader = await user_repo.get_by_id(project.leader_id)
    leader_profile = (
        await profile_repo.get_profile_by_user_id(project.leader_id) if leader is not None else None
    )

    memberships, total = await membership_repo.list_members(project.id, limit=5, offset=0)
    member_user_ids = [m.user_id for m in memberships]
    users_by_id = await user_repo.get_by_ids(member_user_ids) if member_user_ids else {}
    profiles_by_id = (
        await profile_repo.get_profiles_by_user_ids(member_user_ids) if member_user_ids else {}
    )
    examples: list[dict] = []
    for m in memberships:
        u = users_by_id.get(m.user_id)
        if u is None:
            continue
        p = profiles_by_id.get(m.user_id)
        examples.append(
            {
                "id": m.id,
                "user": _user_summary(u, p),
                "role": _PROJECT_ROLE_TO_FRONTEND.get(m.role, "MEMBER"),
                "createdAt": int(m.created_at.timestamp() * 1000) if m.created_at else 0,
                "updatedAt": int(m.updated_at.timestamp() * 1000) if m.updated_at else 0,
            }
        )

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
        "archived": project.archived,
        "team": _team_summary(team),
        "leader": _user_summary(leader, leader_profile),
        "members": {"count": total, "examples": examples},
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
    payload: CreateProjectRequest,
    service: ProjectService = Depends(get_project_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if auth_user.user_id == 0:
        raise ForbiddenError("Authentication required")
    color_code = _validate_color_code(payload.color_code)

    project = await service.create_project(
        name=payload.name.strip(),
        description=payload.description,
        color_code=color_code,
        team_id=payload.team_id,
        leader_id=payload.leader_id,
        start_date=payload.start_date,
        end_date=payload.end_date,
        content=payload.content,
        parent_id=payload.parent_id,
        external_task_id=payload.external_task_id,
        github_repo=payload.github_repo,
    )
    return {
        "code": 201,
        "message": "Created",
        "data": {"project": await _project_to_api_model(project, db=db)},
    }


@router.get(
    "/{projectId}",
    summary="Query Project",
)
async def get_project(
    project_id: Annotated[int, Path(ge=1, alias="projectId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: ProjectService = Depends(get_project_service),
    db: AsyncSession = Depends(get_db),
) -> dict:
    _ = auth_user
    project = await service.get_project(project_id=project_id)
    if project is None:
        raise NotFoundError("Project not found")

    return {
        "code": 200,
        "message": "success",
        "data": {"project": await _project_to_api_model(project, db=db)},
    }


@router.patch(
    "/{projectId}",
    summary="Update Project",
)
async def patch_project(
    project_id: Annotated[int, Path(ge=1, alias="projectId")],
    payload: PatchProjectRequest,
    service: ProjectService = Depends(get_project_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if auth_user.user_id == 0:
        raise ForbiddenError("Authentication required")
    project = await service.get_project(project_id=project_id)
    if project is None:
        raise NotFoundError("Project not found")

    color_code = (
        _validate_color_code(payload.color_code) if payload.color_code is not None else None
    )

    updated = await service.update_project(
        project,
        name=payload.name.strip() if payload.name and payload.name.strip() else None,
        description=payload.description,
        color_code=color_code,
        archived=payload.archived,
    )
    return {
        "code": 200,
        "message": "success",
        "data": {"project": await _project_to_api_model(updated, db=db)},
    }


@router.delete(
    "/{projectId}",
    summary="Delete Project",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_project(
    project_id: Annotated[int, Path(ge=1, alias="projectId")],
    service: ProjectService = Depends(get_project_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> None:
    if auth_user.user_id == 0:
        raise ForbiddenError("Authentication required")
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: ProjectService = Depends(get_project_service),
    db: AsyncSession = Depends(get_db),
) -> dict:
    _ = auth_user
    projects = await service.list_projects(
        team_id=team_id,
        parent_id=parent_id,
        leader_id=leader_id,
        member_id=member_id,
        archived=archived,
    )
    items = [await _project_to_api_model(p, db=db) for p in projects]
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
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
    payload: AddProjectMemberRequest,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: ProjectService = Depends(get_project_service),
) -> dict:
    _ = auth_user
    await _get_project_or_404(service, project_id)

    membership = await service.add_member(
        project_id=project_id,
        user_id=payload.user_id,
        role=payload.role.upper(),
        notes=payload.notes or "",
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
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: ProjectService = Depends(get_project_service),
) -> None:
    _ = auth_user
    await _get_project_or_404(service, project_id)
    await service.remove_member(project_id=project_id, user_id=user_id)
