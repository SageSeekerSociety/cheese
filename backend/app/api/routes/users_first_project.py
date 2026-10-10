"""A new account's first project, made once, so the first page they see is a room.

Someone who signs in owning no project used to land on the inbox and had to find
"new project" before anything could happen. The page now calls this instead
(`frontend/src/router/home.ts`) and opens the project's 综合, where the starter
cards wait (`frontend/src/components/room/StarterCards.vue`).

Once per account, decided here and not by the page: the user row is locked while
the project is made and `first_project_at` is stamped in the same transaction, so
two tabs signing in together, or a retry after a lost answer, never make a
second project. Someone who already had one gets `project_id: null` and the page
falls back to the inbox.

Ordering. This module sorts after the `users` package, so its router mounts
after that package's. `POST /users/me/first-project` has two segments under
`/users`, and no earlier POST route has that shape, so nothing registered before
it can shadow it.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok, typed_response
from app.api.write_access import ROUTE_DECIDES
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.db.session import get_db
from app.domain.project.services import ProjectService
from app.domain.user.services import account_awaiting_first_project

# The signed-in user makes a project for themselves and nobody else:
# `require_auth_user` is the whole check.
router = APIRouter(prefix="/users", tags=["Users"], dependencies=[ROUTE_DECIDES])


class FirstProjectIn(BaseModel):
    # Written by the page in the person's language ("林的项目"); renamed later
    # like any project.
    name: str = Field(min_length=1, max_length=200)


class FirstProjectOut(BaseModel):
    #: The project just made; null when this account was given one before.
    project_id: uuid.UUID | None


@router.post(
    "/me/first-project",
    summary="Give the signed-in user their first project, once per account",
    **typed_response(FirstProjectOut),
)
async def make_my_first_project(
    payload: FirstProjectIn,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    user = await account_awaiting_first_project(session, auth_user.user_id)
    if user is None:
        return ok(FirstProjectOut(project_id=None).model_dump(mode="json"))
    project = await ProjectService(session).create(
        name=payload.name, owner_handle=user.username
    )
    user.first_project_at = datetime.now(UTC)
    # The page opens the project as soon as this answer arrives; the
    # request-scoped commit would land only after the response is sent.
    await session.commit()
    return ok(FirstProjectOut(project_id=project.id).model_dump(mode="json"))
