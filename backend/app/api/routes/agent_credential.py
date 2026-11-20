"""Issue / inspect / revoke a project's agent credential.

The credential lets 芝士 act in this project from outside the platform process —
a local agent, a bot, a CI job. It is the PROJECT's credential: its owner, or an
owner/admin of its team, signs the issue with their own login because someone
accountable has to decide that this project wants an off-platform agent, but
nothing about the credential is theirs afterwards. It does not weaken when they
leave, does not change when the project changes hands, and does not carry their
permissions.

The secret is shown once, at issue. Nothing stores it, so losing one means
issuing a new one (and revoking, which retires the old one along with every
other credential this project has ever handed out).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.api.response import ok
from app.api.write_access import Can, InProject
from app.auth.project_roles import may
from app.core.errors import ForbiddenError, NotFoundError
from app.core.sentences import say
from app.domain.agent_credential.services import ProjectAgentCredentialService
from app.domain.project.repositories import ProjectRepository
from app.domain.project_permissions import Permission

router = APIRouter(prefix="/projects", tags=["agent-credential"])


# Who may decide that this project hands a credential to an off-platform agent:
# whoever may change the project's settings. A member acts for themselves; this
# is a decision about the project.


async def require_credential_manager(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> None:
    """Only a verified person who may change the project's settings manages its
    credentials; an agent never does. Revocation invalidates every credential in
    the project generation.
    """
    actor = await resolver.resolve(project_id=project_id)
    if not actor.authenticated:
        raise ForbiddenError(say("projectCredentialManagerOnly"))
    project = await ProjectRepository(db).get(project_id)
    if project is None:
        raise NotFoundError(say("projectNotFound"))
    if actor.via != "cheese" and await may(
        db, project_id, actor.handle, Permission.edit_settings
    ):
        return
    raise ForbiddenError(say("projectCredentialManagerOnly"))


class IssueCredentialIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    expires_in_days: int | None = Field(default=None, alias="expiresInDays")


def _signed_in(at: InProject) -> uuid.UUID:
    """The project, once ``Can(Permission.edit_settings)`` let in somebody who
    signed in: the development override names nobody, and nobody decides this."""
    if not at.actor.authenticated:
        raise ForbiddenError(say("projectCredentialManagerOnly"))
    return at.project.id


@router.post("/{project_id}/agent-credential")
async def issue_agent_credential(
    at: Annotated[InProject, Can(Permission.edit_settings)],
    body: IssueCredentialIn,
    db: DbSession,
) -> dict:
    """Issue a credential. ``token`` appears in THIS response and nowhere else."""
    project_id = _signed_in(at)
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
    dependencies=[Depends(require_credential_manager)],
)
async def get_agent_credential_status(project_id: uuid.UUID, db: DbSession) -> dict:
    """The project's current credential generation — never a secret. Credentials
    are not listed because none are stored; the generation is the only fact the
    platform keeps about them."""
    epoch = await ProjectAgentCredentialService(db).current_epoch(project_id=project_id)
    return ok({"project_id": str(project_id), "epoch": epoch})


@router.delete("/{project_id}/agent-credential")
async def revoke_agent_credentials(
    at: Annotated[InProject, Can(Permission.edit_settings)], db: DbSession
) -> dict:
    """Revoke — every credential issued for this project stops working on the
    next request. There is no way to revoke just one, on purpose: a project has
    one agent, so "which one" is not a question the model can pose."""
    project_id = _signed_in(at)
    epoch = await ProjectAgentCredentialService(db).revoke(project_id=project_id)
    return ok({"project_id": str(project_id), "epoch": epoch})
