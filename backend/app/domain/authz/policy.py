"""Composable authorization policy (fusion-design §4, 照 reference viewer_authz.py).

Authorization is a **pure策略 + injected adapters** so it is unit-testable without
a DB or WebSocket, and the concrete integrations wire once (``app.api.auth``).

Discipline:
- **A valid token is 必要非充分** — every write is authorized against the actor's
  *real* membership/role, not merely "has a token".
- **权限属于项目** — a project member (or its owner) may act in the project's topics;
  the group is the unit of shared access.
- **话题成员可访问其内容/现场** — a topic-roster member may act in that topic.
- **Backward compatible** — the Phase-0 handle fallback (``actor.authenticated``
  is False) stays permissive so pre-token callers never break; enforcement bites
  only authenticated (token/agent) actors, and only when a roster actually exists.

⚠️ 那条 "stays permissive" 的假设已被现场证伪，两处都在待办上（阶段三）:

1. It is not permissiveness, it is **failure degrading to full allow**. A caller
   presenting a *wrong* credential does not fail closed — it fails to resolve,
   drops to the handle fallback, lands as unauthenticated, and hits line 1 of
   ``authorize_topic_access``. So **presenting the wrong token was strictly more
   permissive than presenting none**. Observed live: a topic's scoped token used
   on a *different* topic's comment route, which wrote a block authored
   ``anonymous``. Closed upstream for that path (``ActorResolver.
   _reject_out_of_scope_token`` 403s an out-of-scope scoped token), but the
   ``if not actor.authenticated: return True`` branch itself is still here.
2. The "no roster yet → legacy topic" escape below is NOT self-converging. 私聊
   topics are created by ``TopicRepository.get_or_create_private`` which never
   seeds a roster, so they land in that escape **by design, permanently** — it is
   not a migration backlog that drains. Any authenticated caller holding a
   private topic's id therefore reads it. Fix belongs at the seam (seed the
   two-person roster / judge ``private_owner``/``private_peer``), not here.
"""

import uuid
from collections.abc import Awaitable, Callable

from app.domain.identity.actor import Actor
from app.domain.project.models import ProjectRole
from app.domain.topic.models import TopicRole

# Injected adapters — each a thin DB read wired in app.api.auth.
TopicRoleReader = Callable[[uuid.UUID, str], Awaitable[TopicRole | None]]
RosterExists = Callable[[uuid.UUID], Awaitable[bool]]  # topic has any members?
ProjectMemberCheck = Callable[[uuid.UUID, str], Awaitable[bool]]  # project, handle
ProjectRoleReader = Callable[[uuid.UUID, str], Awaitable[ProjectRole | None]]
ProjectOwnerReader = Callable[[uuid.UUID], Awaitable[str | None]]

# Project roles allowed to mutate the project roster (add / remove / role). The
# owner is authorized separately — ``projects.owner_handle`` may name someone who
# holds no ProjectMember row at all, and may be NULL, in which case leads are the
# only way the roster stays manageable.
_PROJECT_MANAGER_ROLES = frozenset({ProjectRole.lead})


async def authorize_topic_access(
    actor: Actor,
    *,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    topic_role: TopicRoleReader,
    roster_exists: RosterExists,
    is_project_member: ProjectMemberCheck,
) -> bool:
    """May ``actor`` read/act in this topic's group room?

    Agents (their scoped token already bound them to this project/topic at the
    gate) and the deprecated handle-fallback are allowed; an authenticated human
    must be a topic-roster member OR a project member — unless the topic has no
    roster yet (legacy), which stays open."""
    # Phase-0 fallback and agents pass through (see module docstring).
    if not actor.authenticated or actor.is_agent:
        return True
    # Authenticated human: real membership decides.
    if await topic_role(topic_id, actor.handle) is not None:
        return True
    if await is_project_member(project_id, actor.handle):
        return True
    # No roster exists yet → legacy topic, stay permissive; else it's an outsider.
    # ⚠️ 私聊 topics never get a roster (see module docstring §2), so they sit in
    # this branch forever rather than aging out of it — this line is what lets any
    # authenticated caller read a private topic they were never part of.
    return not await roster_exists(topic_id)


async def can_manage_project_members(
    actor: Actor,
    *,
    project_id: uuid.UUID,
    project_owner: ProjectOwnerReader,
    project_role: ProjectRoleReader,
) -> bool:
    """May ``actor`` add / remove a project member or change their role?

    Only a **verified human** who owns or leads the project. This is the one
    judgment where the Phase-0 handle fallback is deliberately NOT permissive,
    and the exception is load-bearing: the project roster is the floor of topic
    access control — ``authorize_topic_access`` lets *any* project member into
    *every* topic of the project — so honoring a merely *claimed* handle would
    let an anonymous caller write itself into the roster and read the whole
    project. Same reasoning as ``require_quality_gate_admin``: a credential is
    必要 for the writes that decide who else gets in.

    Agents are refused as well, even holding a valid scoped token: a 分身 must
    ask a human to change the roster rather than promote itself. That is not
    theoretical — 芝士 promoting a member to lead through this very surface is
    what exposed the missing check.
    """
    if actor.via != "token" or actor.is_agent:
        return False
    if await project_owner(project_id) == actor.handle:
        return True
    return await project_role(project_id, actor.handle) in _PROJECT_MANAGER_ROLES


async def can_manage_roster(
    actor: Actor,
    *,
    topic_id: uuid.UUID,
    topic_role: TopicRoleReader,
) -> bool:
    """Only a topic owner/admin may mutate its roster (add/remove/role). The
    handle fallback keeps the existing owner/admin check working pre-token.

    ⚠️ Currently UNWIRED — nothing calls this. That is the only reason its
    fallback is not a live hole: the real check runs in
    ``TopicMemberService._require_manager``, which does read the role. Wiring this
    up without first removing the fallback below would open one."""
    if not actor.authenticated:
        return True  # deprecated path — the service still checks the role itself
    role = await topic_role(topic_id, actor.handle)
    return role in (TopicRole.owner, TopicRole.admin)
