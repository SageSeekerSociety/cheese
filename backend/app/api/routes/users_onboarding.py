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
* from the desktop app, someone who wants the AI teammate on this computer sends
  its device id: the computer joins the project and 综合's new agents get a
  sandbox on it (`devices_of`). A computer that is not theirs or cannot make
  a sandbox leaves 综合 on the platform's default, and the answer says so;
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
from app.core.errors import BaseError, NotFoundError
from app.db.session import get_db
from app.domain.agent.environment import Sandbox, devices_of, dump
from app.domain.agent_instance.adapters import default_teammate, project_team
from app.domain.delivery.ledger import deliver, settle
from app.domain.device.wiring import sql_device_service
from app.domain.identity.actor import Actor
from app.domain.machine.environments import check_allowed
from app.domain.membership.services import InvitationService
from app.domain.project.protection import BRANCH_PROTECTION_KEY
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from app.domain.user.about import WorkRole, record_guide_answers
from app.domain.user.services import lookup_account

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
    #: The desktop app's own computer, when the AI teammate should work on it.
    device_id: str | None = Field(default=None, max_length=64)


class OnboardingOut(BaseModel):
    project_id: uuid.UUID
    root_topic_id: uuid.UUID | None
    #: The invitees that could not be invited (no such account, or themselves).
    not_invited: list[str]
    #: 综合's AI teammates work on the computer that was sent.
    on_device: bool


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
        nickname=payload.nickname,
        work_role=payload.work_role,
        organization=payload.organization,
    )
    if user is None:
        raise NotFoundError()

    projects = ProjectService(session, default_teammate=default_teammate)
    project = await projects.create(
        name=payload.project_name,
        owner_handle=user.username,
        intent=payload.intent,
        project_id=payload.project_id,
    )
    if payload.solo:
        await projects.lock_settings(project)
        settings = {**(project.settings or {})}
        rules = settings.get(BRANCH_PROTECTION_KEY)
        settings[BRANCH_PROTECTION_KEY] = {
            **(rules if isinstance(rules, dict) else {}),
            "default_reviewer": user.username,
        }
        project.settings = settings

    me = Actor(handle=user.username, user_id=user.id, via="token")
    # The invitation writes land on the delivery ledger, one layer above
    # ``membership``; this door hands the two in.
    invitations = InvitationService(
        session, deliver=deliver, settle=settle, team=project_team
    )
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
        except BaseError:
            not_invited.append(asked)

    on_device = bool(payload.device_id) and await _work_on_own_computer(
        session, project, payload.device_id or "", me
    )

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
            on_device=on_device,
        ).model_dump(mode="json")
    )


async def _work_on_own_computer(
    session: AsyncSession, project, device_id: str, me: Actor
) -> bool:
    """Attach the person's computer to the project and give 综合's new agents
    a sandbox on their computers. False, with nothing changed, when the
    computer is not theirs or cannot make a sandbox."""
    assert me.user_id is not None
    try:
        async with session.begin_nested():
            await sql_device_service(session).assign_to_project(
                device_id, project.id, actor_user_id=me.user_id
            )
            environment = Sandbox(spec=devices_of(me.user_id))
            await check_allowed(session, project, environment, me)
            room = await TopicService(session).lock_for_execution(project.root_topic_id)
            room.default_environment = dump(environment)
    except BaseError:
        return False
    return True
