"""A project's avatar: what its tile shows wherever the project is listed.

Whoever may edit the project's settings may change it. The rules a chosen
avatar is held to are in ``app/domain/project/avatar.py``.
"""

from typing import Annotated

from fastapi import APIRouter

from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.write_access import Can, InProject
from app.core.errors import ValidationError
from app.core.sentences import say
from app.domain.avatars.services import is_uploaded
from app.domain.project import avatar
from app.domain.project.schemas import ProjectAvatarIn, ProjectAvatarOut
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
