"""The new-user guide's last step: the answers become the person's project.

The guide (`frontend/src/views/welcome/`) asks what to call someone, what they
mainly use the platform for, what they want to get done first, who they do it
with and where the AI teammate works, and shows the project taking shape as they
answer. "建好项目" sends everything here in one request:

* the name, role and school or employer go on the account, where every AI
  teammate reads them on the roster (`app/domain/user/about.py`);
* the project is made with what they want to do as its brief, which every AI
  teammate reads too (`ProjectService.create`, ``intent``);
* someone working alone becomes the project's default reviewer, so their first
  task starts without stopping to ask who reviews it;
* the people they named are invited; anyone who cannot be is returned, and the
  project is made regardless;
* `onboarded_at` is stamped the first time, so the guide is not shown again on
  any device. Opening it again from the help menu makes another project.

The page picks the project id once per pass through the guide, so a retry after
a lost answer finishes the same project instead of making a second one.

Ordering. This module sorts after the `users` package, so its router mounts
after that package's. `POST /users/me/onboarding` has two segments under
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
from app.core.errors import AppError, NotFoundError
from app.db.session import get_db
from app.domain.identity.actor import Actor
from app.domain.membership.services import InvitationService
from app.domain.project.protection import BRANCH_PROTECTION_KEY
from app.domain.project.repositories import ProjectRepository
from app.domain.project.services import ProjectService
from app.domain.user.about import WorkRole, record_guide_answers
from app.domain.user.repositories import UserProfileRepository
from app.domain.user.services import UserProfileService, lookup_account

# The signed-in user answers for themselves and makes their own project:
# `require_auth_user` is the whole check.
router = APIRouter(prefix="/users", tags=["Users"], dependencies=[ROUTE_DECIDES])


class OnboardingIn(BaseModel):
    project_id: uuid.UUID
    project_name: str = Field(min_length=1, max_length=200)
    #: What they want to get done first; the project's brief.
    intent: str = Field(default="", max_length=2000)
    nickname: str | None = Field(default=None, max_length=50)
    work_role: WorkRole | None = None
    organization: str | None = Field(default=None, max_length=100)
    #: Usernames or email addresses of the people to invite.
    invitees: list[str] = Field(default_factory=list, max_length=10)
    #: Nobody else reviews: they review their own AI teammate's work.
    solo: bool = True


class OnboardingOut(BaseModel):
    project_id: uuid.UUID
    root_topic_id: uuid.UUID | None
    #: The invitees that could not be invited (no such account, or themselves).
    not_invited: list[str]


@router.post(
    "/me/onboarding",
    summary="Finish the new-user guide: save the answers and make the project",
    **typed_response(OnboardingOut),
)
async def finish_onboarding(
    payload: OnboardingIn,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    user = await record_guide_answers(
        session,
        auth_user.user_id,
        work_role=payload.work_role,
        organization=payload.organization,
    )
    if user is None:
        raise NotFoundError("User not found")
    if payload.nickname and payload.nickname.strip():
        await UserProfileService(UserProfileRepository(session)).update_profile(
            user_id=user.id, nickname=payload.nickname
        )

    project = await ProjectService(session).create(
        name=payload.project_name,
        owner_handle=user.username,
        intent=payload.intent,
        project_id=payload.project_id,
    )
    if payload.solo:
        repo = ProjectRepository(session)
        await repo.lock_settings(project)
        settings = {**(project.settings or {})}
        rules = settings.get(BRANCH_PROTECTION_KEY)
        settings[BRANCH_PROTECTION_KEY] = {
            **(rules if isinstance(rules, dict) else {}),
            "default_reviewer": user.username,
        }
        project.settings = settings

    me = Actor(handle=user.username, user_id=user.id, via="token")
    invitations = InvitationService(session)
    not_invited: list[str] = []
    for asked in dict.fromkeys(q.strip() for q in payload.invitees if q.strip()):
        account = await lookup_account(session, asked)
        if account is None:
            not_invited.append(asked)
            continue
        try:
            async with session.begin_nested():
                await invitations.invite(
                    project_id=project.id, invitee_handle=account["handle"], actor=me
                )
        except AppError:
            not_invited.append(asked)

    if user.onboarded_at is None:
        user.onboarded_at = datetime.now(UTC)
    # The page opens the project as soon as this answer arrives; the
    # request-scoped commit would land only after the response is sent.
    await session.commit()
    return ok(
        OnboardingOut(
            project_id=project.id,
            root_topic_id=project.root_topic_id,
            not_invited=not_invited,
        ).model_dump(mode="json")
    )
