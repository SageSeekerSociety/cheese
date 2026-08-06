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
"""

import uuid
from collections.abc import Awaitable, Callable

from app.domain.identity.actor import Actor
from app.domain.topic.models import TopicRole

# Injected adapters — each a thin DB read wired in app.api.auth.
TopicRoleReader = Callable[[uuid.UUID, str], Awaitable[TopicRole | None]]
RosterExists = Callable[[uuid.UUID], Awaitable[bool]]  # topic has any members?
ProjectMemberCheck = Callable[[uuid.UUID, str], Awaitable[bool]]  # project, handle


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
    return not await roster_exists(topic_id)


async def can_manage_roster(
    actor: Actor,
    *,
    topic_id: uuid.UUID,
    topic_role: TopicRoleReader,
) -> bool:
    """Only a topic owner/admin may mutate its roster (add/remove/role). The
    handle fallback keeps the existing owner/admin check working pre-token."""
    if not actor.authenticated:
        return True  # deprecated path — the service still checks the role itself
    role = await topic_role(topic_id, actor.handle)
    return role in (TopicRole.owner, TopicRole.admin)
