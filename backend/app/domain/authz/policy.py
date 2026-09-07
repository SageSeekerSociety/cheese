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
   The chat WebSocket no longer reaches that branch at all: it refuses a socket
   it cannot identify (``refuse_unauthenticated_chat``), the same "close the
   entrance, leave the branch for 阶段三" move as ``_reject_out_of_scope_token``.
   Every REST route still takes it.
2. The "no roster yet → legacy topic" escape is valid only for shared legacy
   topics. Private topics now seed their actual participants and bypass that
   escape entirely: access always requires authenticated membership in their
   exact roster.
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
    is_private: bool = False,
) -> bool:
    """May ``actor`` read/act in this topic's group room?

    Agents (their scoped token already bound them to this project/topic at the
    gate) and the deprecated handle-fallback are allowed; an authenticated human
    must be a topic-roster member OR a project member — unless the topic has no
    roster yet (legacy), which stays open. Private topics are the exception:
    authenticated membership in that exact topic is always required."""
    role = await topic_role(topic_id, actor.handle)
    if is_private:
        # Private rooms admit exactly their roster. This also keeps a
        # project-wide agent credential out of human-to-human DMs; the topic's
        # own agent is allowed only when it has the seeded DM seat.
        return actor.authenticated and role is not None
    if actor.is_agent:
        return True
    # Phase-0 fallback stays permissive for non-private legacy surfaces.
    if not actor.authenticated:
        return True
    if role is not None:
        return True
    if await is_project_member(project_id, actor.handle):
        return True
    # No roster exists yet → shared legacy topic, stay permissive; else outsider.
    # Private topics returned above and can never reach this compatibility path.
    return not await roster_exists(topic_id)


def refuse_unauthenticated_chat(
    actor: Actor, *, token_presented: bool, allow_anonymous: bool
) -> tuple[str, str] | None:
    """``(code, message)`` refusing a chat WebSocket, or ``None`` to admit it.

    A WebSocket authenticates ONCE, at connect; the protocol carries no
    per-message credential. So admitting a socket we could not identify means
    every message it later sends is authored by a string the client chose —
    which is how a batch of messages landed under 匿名者 after one user's token
    quietly expired, with the sending side seeing nothing but success frames.

    ``token_presented`` splits the two cases apart, because they are not the
    same failure. A socket that presented a token we could not verify tried to
    authenticate and failed: it is refused unconditionally, since treating a
    rejected credential as "no credential" is precisely the silent downgrade
    above. A socket that presented none is the pre-token Phase-0 caller, and
    ``allow_anonymous`` keeps that path open for local harnesses only.
    """
    if actor.authenticated:
        return None
    if token_presented:
        return ("auth_expired", "登录状态已失效，请重新登录后再发言")
    if allow_anonymous:
        return None
    return ("auth_required", "请先登录再进入话题")


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
    project. Same reasoning as ``require_project_steward``: a credential is
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
