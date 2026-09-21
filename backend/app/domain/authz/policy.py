"""Participant authorization, independent of whether a user is human or an agent.

Verified room members and project members can access shared rooms. Private
rooms require exact room membership. Missing credentials or an empty roster
grant nothing. Credential scope is checked by ActorResolver before this policy.
Project membership management requires the project's owner or lead role.
"""

import uuid
from collections.abc import Awaitable, Callable

from app.domain.identity.actor import Actor
from app.domain.project.models import ProjectRole
from app.domain.topic.models import TopicRole

# Injected adapters — each a thin DB read wired in app.api.auth.
TopicRoleReader = Callable[[uuid.UUID, str], Awaitable[TopicRole | None]]
ProjectMemberCheck = Callable[[uuid.UUID, str], Awaitable[bool]]  # project, handle
ProjectRoleReader = Callable[[uuid.UUID, str], Awaitable[ProjectRole | None]]
ProjectOwnerReader = Callable[[uuid.UUID], Awaitable[str | None]]
AgentBindingCheck = Callable[[str], Awaitable[bool]]  # handle → carries a binding?

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
    is_project_member: ProjectMemberCheck,
    is_private: bool = False,
) -> bool:
    """May ``actor`` read/act in this topic's group room?

    Authenticated people and agents need room or project membership. Private
    rooms require membership in that exact room. Credential scope is checked
    separately at the request boundary."""
    if not actor.authenticated:
        return False
    role = await topic_role(topic_id, actor.handle)
    if is_private:
        # Project membership never grants access to someone else's private room.
        return actor.authenticated and role is not None
    if role is not None:
        return True
    if await is_project_member(project_id, actor.handle):
        return True
    return False


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


async def refuse_management_action(
    actor: Actor, *, carries_agent_binding: AgentBindingCheck
) -> str | None:
    """The refusal a management surface owes, or ``None`` to let ``actor`` in.

    Management is what a person does in their own session, so the refusal is two
    questions of two different things, and neither subsumes the other:

    - the CREDENTIAL. A scoped credential (``via="cheese"``) is a per-request
      capability handed to something running unattended — a device screen's
      ``X-Cheese-Screen`` token resolves on any path, including one with no
      topic in it. Whoever holds it is not sitting in their own session, so it
      cannot carry a management action whatever the handle turns out to be.
      This is a SCOPE question, which is the credential's own business (see the
      module docstring): it does not type the participant, and a handle with no
      agent-binding arriving this way is refused just the same.
    - the PARTICIPANT. Whether the handle carries an agent-binding, asked of the
      binding through the injected adapter rather than read off the actor. An
      allow-list is a list of handles and nothing stops an agent's from being on
      one, so without this the binding would be the one thing nobody asked.

    It lives here rather than in the route because "what may this actor do" has
    one answer per question in this codebase, and this is a question — the route
    calls it. Where the refusal is *reached from* is a separate matter and does
    stay in the route body: ``/admin/*`` is not in ``_CHEESE_WRITE_PATHS``, and
    that table is a whitelist, so nothing in the middleware looks at that prefix
    and a refusal written there would not exist.
    """
    if actor.via == "cheese":
        return "作用域凭证不能执行管理动作，请用本人会话"
    if await carries_agent_binding(actor.handle):
        return "agent 不能执行管理动作"
    return None


async def can_manage_project_members(
    actor: Actor,
    *,
    project_id: uuid.UUID,
    project_owner: ProjectOwnerReader,
    project_role: ProjectRoleReader,
) -> bool:
    """May ``actor`` add / remove a project member or change their role?

    A verified participant must own or lead the project. Credentials prove
    identity, not management authority; a member cannot promote itself.
    """
    if not actor.authenticated:
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
