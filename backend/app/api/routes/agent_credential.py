"""Issue / inspect / revoke a project's agent credential.

The credential lets 芝士 act in this project from outside the platform process —
a local agent, a bot, a CI job. It is the PROJECT's credential: an owner or lead
signs the issue with their own login because someone accountable has to decide
that this project wants an off-platform agent, but nothing about the credential
is theirs afterwards. It does not weaken when they leave, does not change when
the project changes leads, and does not carry their permissions.

The secret is shown once, at issue. Nothing stores it, so losing one means
issuing a new one (and revoking, which retires the old one along with every
other credential this project has ever handed out).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.response import ok
from app.core.db import get_db
from app.core.errors import ForbiddenError, NotFoundError
from app.domain.agent_credential.services import ProjectAgentCredentialService
from app.domain.membership.repositories import MemberRepository
from app.domain.project.models import ProjectRole
from app.domain.project.repositories import ProjectRepository

router = APIRouter(prefix="/projects", tags=["agent-credential"])

DbSession = Annotated[AsyncSession, Depends(get_db)]

# Who may decide that this project hands a credential to an off-platform agent.
# Deliberately narrower than "member": a member acts for themselves, these two
# answer for the project, and this is a decision about the project.
_STEWARD_ROLES = frozenset({ProjectRole.lead})


async def require_project_steward(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> None:
    """Only a verified project owner/lead may manage credentials.

    An agent needs the same role. Revocation invalidates every credential in
    the project generation, including any issued by an authorized agent.
    """
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    if not actor.authenticated:
        raise ForbiddenError("只有项目的 owner / lead 能管理项目凭证")
    project = await ProjectRepository(db).get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    if project.owner_handle == actor.handle:
        return
    member = await MemberRepository(db).get(
        project_id=project_id, user_handle=actor.handle
    )
    if member is not None and member.role in _STEWARD_ROLES:
        return
    raise ForbiddenError("只有项目的 owner / lead 能管理项目凭证")


class IssueCredentialIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    expires_in_days: int | None = Field(default=None, alias="expiresInDays")


@router.post(
    "/{project_id}/agent-credential",
    dependencies=[Depends(require_project_steward)],
)
async def issue_agent_credential(
    project_id: uuid.UUID, body: IssueCredentialIn, db: DbSession
) -> dict:
    """Issue a credential. ``token`` appears in THIS response and nowhere else."""
    issued = await ProjectAgentCredentialService(db).issue(
        project_id=project_id, expires_in_days=body.expires_in_days
    )
    return ok(
        {
            "token": issued.token,
            "project_id": str(issued.project_id),
            "agent_handle": await ProjectAgentCredentialService(db).agent_handle(
                project_id
            ),
            "epoch": issued.epoch,
            "expires_at": issued.expires_at.isoformat(),
        }
    )


@router.get(
    "/{project_id}/agent-credential",
    dependencies=[Depends(require_project_steward)],
)
async def get_agent_credential_status(project_id: uuid.UUID, db: DbSession) -> dict:
    """The project's current credential generation — never a secret. Credentials
    are not listed because none are stored; the generation is the only fact the
    platform keeps about them."""
    epoch = await ProjectAgentCredentialService(db).current_epoch(project_id=project_id)
    return ok({"project_id": str(project_id), "epoch": epoch})


@router.delete(
    "/{project_id}/agent-credential",
    dependencies=[Depends(require_project_steward)],
)
async def revoke_agent_credentials(project_id: uuid.UUID, db: DbSession) -> dict:
    """Revoke — every credential issued for this project stops working on the
    next request. There is no way to revoke just one, on purpose: a project has
    one agent, so "which one" is not a question the model can pose."""
    epoch = await ProjectAgentCredentialService(db).revoke(project_id=project_id)
    return ok({"project_id": str(project_id), "epoch": epoch})
