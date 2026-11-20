"""A project's name and avatar: what it is called and what its tile shows
wherever it is listed (project settings, 「名称和头像」).

Whoever may edit the project's settings may change either. A name is held to
what a new project's name is held to (``ProjectCreate``); the rules a chosen
avatar is held to are in ``app/domain/project/avatar.py``. Renaming leaves the
short name in addresses (``slug``) alone: links keep working.
"""

from typing import Annotated

from fastapi import APIRouter

from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.write_access import Can, InProject
from app.core.errors import ValidationError
from app.core.sentences import say
from app.domain.avatars.services import is_uploaded
from app.domain.deliverables import artifacts
from app.domain.project import avatar
from app.domain.project.schemas import (
    ProjectAvatarIn,
    ProjectAvatarOut,
    ProjectNameIn,
    ProjectNameOut,
)
from app.domain.project_permissions import Permission

router = APIRouter(prefix="/projects", tags=["projects"])

_REFUSALS = {
    "hue": "projectAvatarHue",
    "icon": "projectAvatarIcon",
    "emoji": "projectAvatarEmoji",
    "text": "projectAvatarText",
    "image": "projectAvatarImage",
}


@router.put("/{project_id}/avatar", **typed_response(ProjectAvatarOut))
async def set_project_avatar(
    at: Annotated[InProject, Can(Permission.edit_settings)],
    body: ProjectAvatarIn,
    db: DbSession,
) -> dict:
    """Replace the project's avatar. A body without ``kind`` puts the default
    tile back, keeping ``hue`` if one is given."""
    try:
        choice = avatar.checked(body.kind, body.glyph, body.hue, body.image_id)
    except avatar.AvatarRefused as refused:
        raise ValidationError(say(_REFUSALS[refused.reason])) from refused
    if choice.image_id is not None and not await is_uploaded(db, choice.image_id):
        raise ValidationError(say("projectAvatarImage"))
    avatar.apply(at.project, choice)
    await db.commit()
    return ok(ProjectAvatarOut.of(at.project).model_dump(mode="json"))


@router.put("/{project_id}/name", **typed_response(ProjectNameOut))
async def rename_project(
    at: Annotated[InProject, Can(Permission.edit_settings)],
    body: ProjectNameIn,
    db: DbSession,
) -> dict:
    """Rename the project. Its short name, repository and everything keyed by
    its id stay as they are. The repository's line in the list of what the
    project made carries the project's name until someone names it, so it
    follows."""
    was = at.project.name
    at.project.name = body.name
    await artifacts.follow_project_rename(
        db, project_id=at.project.id, was=was, now=body.name
    )
    await db.commit()
    return ok({"id": str(at.project.id), "name": at.project.name})
