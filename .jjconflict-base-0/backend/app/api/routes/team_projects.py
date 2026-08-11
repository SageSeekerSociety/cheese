"""知是 team projects — flat int-keyed routes at /projects (reference:
cheese-backend-nt ProjectController). The cheesex agent workspace lives at
/api/projects (uuid) and is unrelated."""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.db.session import get_db
from app.domain.team_project.services import TeamProjectService

router = APIRouter(prefix="/projects", tags=["TeamProjects"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
AuthUser = Annotated[AuthUserInfo, Depends(require_auth_user)]


def _service(db: AsyncSession) -> TeamProjectService:
    return TeamProjectService(db)


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., min_length=1)
    description: str = ""
    color_code: str = Field(default="#000000", alias="colorCode", max_length=7)
    start_date: int = Field(..., alias="startDate")
    end_date: int = Field(..., alias="endDate")
    team_id: int = Field(..., alias="teamId")
    leader_id: int = Field(..., alias="leaderId")
    parent_id: int | None = Field(default=None, alias="parentId")
    external_task_id: int | None = Field(default=None, alias="externalTaskId")
    github_repo: str | None = Field(default=None, alias="githubRepo")
    member_ids: list[int] = Field(default_factory=list, alias="memberIds")
    external_collaborator_ids: list[int] = Field(
        default_factory=list, alias="externalCollaboratorIds"
    )


class AddMemberRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: int = Field(..., alias="userId")
    role: str = "MEMBER"
    notes: str | None = None


@router.post("")
async def create_project(
    body: CreateProjectRequest,
    db: DbSession,
    auth_user: AuthUser,
) -> dict:
    project = await _service(db).create_project(
        actor_id=auth_user.user_id,
        name=body.name,
        description=body.description,
        color_code=body.color_code,
        start_date_ms=body.start_date,
        end_date_ms=body.end_date,
        team_id=body.team_id,
        leader_id=body.leader_id,
        parent_id=body.parent_id,
        external_task_id=body.external_task_id,
        github_repo=body.github_repo,
        member_ids=body.member_ids,
        external_collaborator_ids=body.external_collaborator_ids,
    )
    return {"code": 201, "message": "Created", "data": {"project": project}}


@router.get("")
async def list_projects(
    db: DbSession,
    auth_user: AuthUser,
    team_id: int = Query(..., alias="team_id"),
    parent_id: int | None = Query(default=None, alias="parent_id"),
    leader_id: int | None = Query(default=None, alias="leader_id"),
    member_id: int | None = Query(default=None, alias="member_id"),
    archived: bool | None = Query(default=None),
) -> dict:
    projects = await _service(db).enumerate_projects(
        team_id=team_id,
        parent_id=parent_id,
        leader_id=leader_id,
        member_id=member_id,
        archived=archived,
    )
    return {"code": 200, "message": "OK", "data": {"projects": projects}}


@router.get("/{projectId}")
async def get_project(
    db: DbSession,
    auth_user: AuthUser,
    project_id: Annotated[int, Path(alias="projectId")],
) -> dict:
    project = await _service(db).get_project(project_id)
    return {"code": 200, "message": "OK", "data": {"project": project}}


@router.patch("/{projectId}")
async def patch_project(
    db: DbSession,
    auth_user: AuthUser,
    project_id: Annotated[int, Path(alias="projectId")],
    fields: dict = Body(default={}),
) -> dict:
    project = await _service(db).patch_project(project_id, auth_user.user_id, fields)
    return {"code": 200, "message": "OK", "data": {"project": project}}


@router.delete("/{projectId}", status_code=204)
async def delete_project(
    db: DbSession,
    auth_user: AuthUser,
    project_id: Annotated[int, Path(alias="projectId")],
) -> Response:
    await _service(db).delete_project(project_id, auth_user.user_id)
    return Response(status_code=204)


@router.get("/{projectId}/members")
async def list_project_members(
    db: DbSession,
    auth_user: AuthUser,
    project_id: Annotated[int, Path(alias="projectId")],
) -> dict:
    members = await _service(db).get_members(project_id)
    return {"code": 200, "message": "OK", "data": {"members": members}}


@router.post("/{projectId}/members")
async def add_project_member(
    body: AddMemberRequest,
    db: DbSession,
    auth_user: AuthUser,
    project_id: Annotated[int, Path(alias="projectId")],
) -> dict:
    member = await _service(db).add_member(
        project_id,
        auth_user.user_id,
        user_id=body.user_id,
        role=body.role,
        notes=body.notes,
    )
    return {"code": 201, "message": "Created", "data": {"member": member}}


@router.delete("/{projectId}/members/{userId}", status_code=204)
async def remove_project_member(
    db: DbSession,
    auth_user: AuthUser,
    project_id: Annotated[int, Path(alias="projectId")],
    user_id: Annotated[int, Path(alias="userId")],
) -> Response:
    await _service(db).remove_member(project_id, auth_user.user_id, user_id)
    return Response(status_code=204)
