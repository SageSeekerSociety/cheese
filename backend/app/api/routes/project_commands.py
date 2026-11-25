"""A project's named commands.

`GET /projects/{project_id}/commands`: the commands fixed under a name in the
project, for everyone in it.

`PUT /projects/{project_id}/commands`: fix a command under a name. A name the
project already has now names this command.

`DELETE /projects/{project_id}/commands/{command_id}`: take one off.

Fixing one is part of doing the project's work (`Permission.tasks`): the same
people who open and review tasks.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, StringConstraints

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.write_access import Can, InProject
from app.domain.project.services import ProjectService
from app.domain.project_command import services
from app.domain.project_command.services import COMMAND_MAX, NAME_MAX
from app.domain.project_permissions import Permission

router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectCommandOut(BaseModel):
    id: str
    name: str
    command: str
    fixed_by: str
    fixed_at: str


class ProjectCommandsOut(BaseModel):
    items: list[ProjectCommandOut]


class ProjectCommandIn(BaseModel):
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=NAME_MAX)
    ]
    command: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=COMMAND_MAX),
    ]


@router.get("/{project_id}/commands", **typed_response(ProjectCommandsOut))
async def list_commands(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    await ProjectService(db).get_or_404(project_id)
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    rows = await services.of_project(db, project_id)
    return ok({"items": rows})


@router.put("/{project_id}/commands", **typed_response(ProjectCommandOut))
async def fix_command(
    at: Annotated[InProject, Can(Permission.tasks)],
    body: ProjectCommandIn,
    db: DbSession,
) -> dict:
    row = await services.fix(
        db, at.project.id, name=body.name, command=body.command, by=at.actor.handle
    )
    await db.commit()
    return ok(row)


class RemovedOut(BaseModel):
    removed: bool


@router.delete("/{project_id}/commands/{command_id}", **typed_response(RemovedOut))
async def remove_command(
    at: Annotated[InProject, Can(Permission.tasks)],
    command_id: uuid.UUID,
    db: DbSession,
) -> dict:
    await services.remove(db, at.project.id, command_id)
    await db.commit()
    return ok({"removed": True})
