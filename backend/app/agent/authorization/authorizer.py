"""The orchestrator -> business authz choke point (architecture doc §4.3).

A ``ProjectActor`` (a user, or an agent acting inside a project) is authorized
by **reusing the existing** ``permission_checker`` — never a parallel model.
For an action to be allowed, some *human identity* that counts must really hold
the permission:

- the actor's own identity (user), or the human an agent acts on behalf of;
- **plus** any holder who shared a covering capability with the project — but
  a share is re-checked live against that holder's *current* permission, so it
  evaporates the moment the holder loses it (capability delegation, §4.2).

Agents get one extra gate first: the project's ``ai_mode`` (OFF blocks every
agent regardless of the underlying permissions). Power/responsibility between
agents is NOT enforced here — it is prompt-conveyed (§4.4); every agent shares
the project's full permissions.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.checker import PermissionChecker, permission_checker
from app.auth.core import Action, AuthUserInfo, Resource
from app.core.errors import PermissionDeniedError
from app.domain.grant.services import ProjectGrantService
from app.domain.project.models import ProjectAiMode
from app.domain.project.repositories import ProjectRepository


@dataclass(frozen=True)
class ProjectActor:
    """Who is acting inside a project.

    This is the one thing that must never come from agent-supplied data — the
    orchestrator/connector injects it at the trust boundary.
    """

    kind: str  # "user" | "agent"
    actor_id: int  # user id or agent id
    project_id: int
    on_behalf_of_user_id: int | None = None


class ProjectAuthorizer:
    def __init__(
        self,
        grant_service: ProjectGrantService,
        project_repo: ProjectRepository,
        checker: PermissionChecker = permission_checker,
    ) -> None:
        self._grants = grant_service
        self._projects = project_repo
        self._checker = checker

    async def _agent_gate_open(self, db: AsyncSession, project_id: int) -> bool:
        project = await self._projects.get_by_id(project_id)
        return project is not None and project.ai_mode != ProjectAiMode.OFF.value

    async def ensure_agent_gate(self, db: AsyncSession, actor: ProjectActor) -> None:
        """The universal agent gate: a project with AI off lets no agent act,
        for ANY action (even 2.0-native ones outside the RBAC engine). No-op for
        user actors. Raises PermissionDeniedError when closed.

        NOTE: this enforces ai_mode only. The ASSISTED ``approval_policy``
        (per-action human approval) is enforced orchestrator-side — it gates
        whether an action is dispatched / a token minted — not here.
        """
        if actor.kind == "agent" and not await self._agent_gate_open(db, actor.project_id):
            raise PermissionDeniedError(f"project {actor.project_id} has AI turned off")

    async def is_allowed(
        self,
        db: AsyncSession,
        actor: ProjectActor,
        action: Action,
        resource: Resource,
        resource_id: int,
    ) -> bool:
        # Agent gate: AI off => no agent may act, whatever permissions say.
        if actor.kind == "agent" and not await self._agent_gate_open(db, actor.project_id):
            return False

        # Human identities whose real permission counts.
        candidates: list[int] = []
        if actor.kind == "user":
            candidates.append(actor.actor_id)
        elif actor.on_behalf_of_user_id is not None:
            candidates.append(actor.on_behalf_of_user_id)

        for granter in await self._grants.granters_covering(
            project_id=actor.project_id,
            resource_type=resource.value,
            action=action.value,
            resource_id=resource_id,
        ):
            if granter not in candidates:
                candidates.append(granter)

        # Live re-check: a share is only worth what its granter currently holds.
        # NOTE: called without a permission `context`, so a context-dependent
        # PermissionRule (e.g. owner_only) evaluates False here — this only ever
        # fails CLOSED (never over-grants). Thread a resource context through
        # once a permission-declaring tool targets such a resource.
        for uid in candidates:
            info = AuthUserInfo(user_id=uid)
            if await self._checker.check_permission(db, info, action, resource, resource_id):
                return True
        return False

    async def authorize(
        self,
        db: AsyncSession,
        actor: ProjectActor,
        action: Action,
        resource: Resource,
        resource_id: int,
    ) -> None:
        """Raise PermissionDeniedError unless the actor is allowed."""
        if not await self.is_allowed(db, actor, action, resource, resource_id):
            raise PermissionDeniedError(
                f"actor {actor.kind}:{actor.actor_id} may not {action.value} "
                f"{resource.value}:{resource_id} in project {actor.project_id}"
            )
