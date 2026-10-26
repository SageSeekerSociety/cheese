"""门面：`app.api.auth` 仍是既有调用点与路由依赖的导入路径。

拆开的起因只是长度：`ActorResolver` 连它的授权方法搬去了
:mod:`app.api.auth.resolver`。接线留在这一层 —— 路由 `Depends` 的
``ActorResolverDep`` / ``MediaActorResolverDep``、``DELEGATED_ROUTES``、
media cookie 和 ``require_seated_agent``。

被测试直接替换的名字必须留在这里，而且只在这里：``UserRepository`` 与
``TopicMemberService`` 是 ``tests/unit/test_actor_numeric_handle.py``、
``tests/unit/test_token_scope_violation.py``、``tests/unit/test_fetch_service.py``
里 ``monkeypatch.setattr(auth_mod, ...)`` 换掉的本体，``ActorResolver``
本身也按类属性被换过（``tests/integration/test_reactions.py`` 换 ``resolve``）。
``resolver.py`` 一律经 ``pkg.`` 在调用时回读它们，自己导入一份副本会让
替换点静默失效。
"""

import uuid
from typing import Annotated

from fastapi import Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth.resolver import ActorResolver as ActorResolver
from app.common.auth import verify_access_token
from app.core.config import GATEWAY_MOUNT, settings
from app.core.db import get_db
from app.core.errors import (
    AuthenticationRequiredError,
    ForbiddenError,
    NotFoundError,
)
from app.core.sandbox_auth import scoped_token_claims
from app.core.sentences import say
from app.domain.conversation import services as conversations
from app.domain.identity.actor import TokenIdentity
from app.domain.topic_membership.services import (
    TopicMemberService as TopicMemberService,
)
from app.domain.user.repositories import UserRepository as UserRepository

#: The routes a delegated credential opens (`sandbox_auth.DelegatedClaims`), by
#: endpoint, and whether each changes something. Every other route refuses it:
#: a 芝士 answering for someone acts through these and nothing else, so a new
#: tool for it is a new line here, read by whoever reviews it.
DELEGATED_ROUTES: dict[str, bool] = {
    "app.api.routes.living_docs.get_document": False,
    "app.api.routes.living_docs.edit_doc_passages": True,
    # Only a blank document: the route refuses a delegated write over content.
    "app.api.routes.living_docs.replace_document": True,
    "app.api.routes.doc_apps.get_app": False,
    "app.api.routes.doc_apps.get_app_version": False,
    "app.api.routes.doc_apps.create_app": True,
    "app.api.routes.doc_apps.add_app_version": True,
    "app.api.routes.project_context.search_project_context": False,
    "app.api.routes.topics_preview.preview_file": False,
    "app.api.routes.library.library_file_text": False,
    "app.api.routes.memory_files.list_memory_files": False,
    "app.api.routes.docs_site.agent_search_docs": False,
    "app.api.routes.docs_site.agent_read_docs": False,
    "app.api.routes.tasks.participation.list_joined_tasks": False,
    "app.api.routes.tasks.drafts.agent_search": False,
    "app.api.routes.tasks.drafts.agent_read": False,
    "app.api.routes.tasks.drafts.agent_write": True,
    "app.api.routes.tasks.drafts.agent_publish": True,
    "app.api.routes.tasks.drafts.agent_file": False,
    "app.api.routes.tasks.drafts.agent_materials": False,
    # A person's own 芝士 in a project: it reads the channels the person may,
    # and drafts what they would say there; the person sends it.
    "app.api.routes.topics.read_chat_history": False,
    "app.api.routes.topics.read_chat_message": False,
    # A person's own 芝士, wherever the person may: what waits on them, a
    # thing by its ref, a web page.
    "app.api.routes.assistant_agent.agent_inbox": False,
    "app.api.routes.assistant_agent.agent_read": False,
    "app.api.routes.project_context.agent_search": False,
    # Searching the web without its gate, unlike reading a page: the query goes
    # only to DeepSeek, which as the model's provider already holds the whole
    # conversation, so a search reaches no one new. A page's address reaches
    # whatever host it names (`read_url` in GATED_ROUTES).
    "app.api.routes.fetch.web_search": False,
    # What it does that others see goes through its gate first, which hands
    # back a pass for the routes in `GATED_ROUTES`.
    "app.api.routes.assistant_agent.agent_act": True,
    "app.api.routes.assistant_agent.agent_wait": False,
    # What is the person's own — their notification settings, how much of a
    # channel reaches them — it changes directly, as a change they can undo.
    "app.api.routes.assistant_agent.agent_notify": True,
    "app.api.routes.notification_preferences.agent_read_preferences": False,
    "app.api.routes.notification_preferences.agent_set_preference": True,
    # What it does for the person on its own later — routines —, telling them
    # something, and what it remembers of them: theirs alone, done directly.
    "app.api.routes.assistant_agent.agent_routine": True,
    "app.api.routes.assistant_agent.agent_notify_me": True,
    "app.api.routes.assistant_agent.agent_memory": True,
    # The person's own library: what they attached and what it hands back,
    # moved between it and its computer at addresses good for one file and a
    # few minutes (`assistant_agent._my_library`).
    "app.api.routes.assistant_agent.agent_library": False,
    "app.api.routes.assistant_agent.agent_file_link": False,
    "app.api.routes.assistant_agent.agent_file_slot": True,
    "app.api.routes.assistant_agent.agent_file_part": True,
    "app.api.routes.assistant_agent.agent_file_handed": True,
}


#: The routes a person's own 芝士 reaches only with a pass its gate issued for
#: that call (`assistant.gate`, `assistant.passes`): what others see — a
#: message, a task, a decision, a member, a channel — and reading a web page,
#: whose address can carry what it read out to the host it names (a page on a
#: documentation site Claude Code reads freely gets its pass with no look,
#: ``fetch.preapproved``). The pass is
#: the gate's verdict; without one the route refuses the credential.
GATED_ROUTES: frozenset[str] = frozenset(
    {
        "app.api.routes.topics_messages.send_chat_message",
        "app.api.routes.topics_tasks.create_task",
        "app.api.routes.accept.accept_card",
        "app.api.routes.accept.reject_card",
        "app.api.routes.alerts.resolve_notification",
        "app.api.routes.members.respond_to_invitation",
        "app.api.routes.members.respond_to_transfer",
        "app.api.routes.members.invite_member",
        "app.api.routes.members.remove_member",
        "app.api.routes.members.set_member_role",
        "app.api.routes.topic_members.add_topic_member",
        "app.api.routes.topic_members.remove_topic_member",
        "app.api.routes.topics.create_topic",
        "app.api.routes.local_mcp.decide",
        "app.api.routes.users_team.accept_team_invitation",
        "app.api.routes.users_team.decline_team_invitation",
        "app.api.routes.teams.approve_team_join_request",
        "app.api.routes.teams.reject_team_join_request",
        "app.api.routes.teams.create_team_invitation",
        "app.api.routes.teams.patch_team_member_role",
        "app.api.routes.teams.delete_team_member",
        "app.api.routes.spaces.add_space_member",
        "app.api.routes.spaces.delete_space_member",
        "app.api.routes.spaces_organization.add_space_admin",
        "app.api.routes.spaces_organization.delete_space_admin",
        "app.api.routes.fetch.read_url",
        "app.api.routes.spaces.patch_space",
        "app.api.routes.space_announcements.publish_announcement",
        "app.api.routes.space_announcements.patch_announcement",
        "app.api.routes.space_announcements.delete_announcement",
        "app.api.routes.spaces_organization.create_space_category",
        "app.api.routes.spaces_organization.patch_space_category",
        "app.api.routes.spaces_organization.delete_space_category",
        "app.api.routes.spaces_organization.archive_space_category",
        "app.api.routes.spaces_organization.unarchive_space_category",
        "app.api.routes.spaces_invite_codes.create_space_invite_code",
        "app.api.routes.spaces_invite_codes.patch_space_invite_code",
        "app.api.routes.spaces_invite_codes.revoke_space_invite_code",
        "app.api.routes.tasks.lifecycle.patch_task",
        "app.api.routes.project_channels.hand_over_channel",
        "app.api.routes.addresses.set_project_slug",
        "app.api.routes.projects.set_task_naming",
        "app.api.routes.projects_agents.set_project_default_agent",
        "app.api.routes.projects_run_config.save_default_model",
    }
)


def _bearer(header: str | None) -> str | None:
    """Extract the token from an ``Authorization: Bearer <token>`` header."""
    if not header:
        return None
    parts = header.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def _token_verifier(token: str) -> TokenIdentity | None:
    claims = verify_access_token(token)
    if claims is None:
        return None
    # The user id is kept alongside the handle: device binding
    # (device.owner_user_id, an int column) needs it.
    return TokenIdentity(handle=claims.handle, user_id=claims.user_id)


def _resolver_for(
    request: Request, db: AsyncSession, bearer: str | None
) -> ActorResolver:
    return ActorResolver(
        session=db,
        bearer=bearer,
        cheese_token=request.headers.get("x-cheese-token") or "",
        screen_token=request.headers.get("x-cheese-screen") or "",
        writes=request.method not in ("GET", "HEAD", "OPTIONS"),
        endpoint=_endpoint_name(request),
        gate_pass=request.headers.get("x-cheese-pass") or "",
        path_ids=frozenset(str(v) for v in request.path_params.values()),
    )


def get_actor_resolver(
    request: Request, db: Annotated[AsyncSession, Depends(get_db)]
) -> ActorResolver:
    return _resolver_for(request, db, _bearer(request.headers.get("authorization")))


#: The cookie a ``<video>`` element's own requests prove the viewer with. The
#: element streams a recording by asking for one byte range after another and
#: cannot add an ``Authorization`` header to any of them, so the one fetch the
#: page makes with the header leaves this cookie behind, scoped to that room's
#: raw-attachment path (``topics_attachments.attachment_raw``). It carries the
#: same access token the page already holds — it grants nothing new and dies
#: with that token — and it is read on that one read-only route alone.
MEDIA_COOKIE = "cheese_media"


def remember_for_media(response: Response, request: Request) -> None:
    """Leave :data:`MEDIA_COOKIE` behind for the ``<video>`` element's own
    requests to this same path, carrying the token this request was made with.

    Only a token sent as ``Authorization`` is mirrored: a cookie is never
    turned into a fresh cookie, so it cannot outlive the page's own token.
    """
    token = _bearer(request.headers.get("authorization"))
    if token:
        response.set_cookie(
            MEDIA_COOKIE,
            token,
            path=f"{GATEWAY_MOUNT}{request.url.path}",
            max_age=settings.access_token_expires_seconds,
            httponly=True,
            secure=settings.environment not in ("development", "test"),
            samesite="lax",
        )


def get_media_actor_resolver(
    request: Request, db: Annotated[AsyncSession, Depends(get_db)]
) -> ActorResolver:
    """``get_actor_resolver`` that also takes the :data:`MEDIA_COOKIE` when the
    request carries no ``Authorization`` header."""
    bearer = _bearer(request.headers.get("authorization"))
    return _resolver_for(request, db, bearer or request.cookies.get(MEDIA_COOKIE))


def _endpoint_name(request: Request) -> str | None:
    endpoint = request.scope.get("endpoint")
    if endpoint is None:
        return None
    return f"{endpoint.__module__}.{endpoint.__qualname__}"


# Browsers cannot set an Authorization header on a WebSocket, so the chat route
# builds the resolver from the ?token= query param itself (照 reference
# viewer_authz.py) — see app/api/routes/chat.py.
ActorResolverDep = Annotated[ActorResolver, Depends(get_actor_resolver)]
MediaActorResolverDep = Annotated[ActorResolver, Depends(get_media_actor_resolver)]


async def session_in(
    session: AsyncSession, token: str, topic_id: uuid.UUID
) -> tuple[uuid.UUID, dict]:
    """The project of a session's own request about the conversation it works
    in — a room, or one of its tasks — and its credential's claims. Only that
    conversation's credential, of that project, with its agent still seated."""
    claims = scoped_token_claims(token)
    if not claims or claims.get("t") != str(topic_id):
        raise AuthenticationRequiredError("A credential for this room is required")
    project_id = await conversations.project_of(session, topic_id)
    if project_id is None:
        raise NotFoundError(say("topicNotFound"))
    if claims.get("p") != str(project_id):
        raise ForbiddenError("This room belongs to another project")
    await require_seated_agent(session, token, project_id=project_id, topic_id=topic_id)
    return project_id, claims


async def require_seated_agent(
    session: AsyncSession,
    token: str,
    *,
    project_id: uuid.UUID,
    topic_id: uuid.UUID | None,
) -> None:
    """Refuse a scoped credential whose agent no longer sits where it acts.

    For routes that read the credential themselves instead of resolving an
    actor. A signature proves which agent a credential was minted for and that
    it has not expired; it says nothing about whether that agent was since
    taken out of the room or the project, and a credential outlives the turn
    that minted it. So the roster is asked here, the same question
    ``authorize_topic`` asks for every other room route.

    A credential naming neither an agent nor a room is the platform's own
    project capability (see ``mint_scoped_token``): no participant, no seat to
    check, so it passes unchanged.
    """
    claims = scoped_token_claims(token)
    if claims is None:
        raise AuthenticationRequiredError("Agent credential is invalid or expired")
    if not claims.get("a") and not claims.get("t"):
        return
    # The room this request acts in; without one (a project-wide route), the
    # room the credential was minted in. An agent holds no project-roster
    # grant, so its seat in that room is the only standing it has.
    room = topic_id
    if room is None and claims.get("t"):
        room = uuid.UUID(claims["t"])
    resolver = ActorResolver(session=session, bearer=None, cheese_token=token)
    actor = await resolver.resolve(topic_id=room, project_id=project_id, read_only=True)
    if room is not None:
        await resolver.authorize_topic(
            actor, project_id=project_id, topic_id=room, enforce=True
        )
    elif not await resolver._is_project_member(project_id, actor.handle):
        raise ForbiddenError(say("projectMemberOnly"))


async def require_seated_in_its_room(
    session: AsyncSession, token: str, *, project_id: uuid.UUID
) -> None:
    """``require_seated_agent`` for the routes that name no room, asked of the
    room the credential was minted in.

    A credential that names no room has no roster to be taken off: the
    platform's own project capability, or the credential of a document that
    sits in no room, which its project's agent answers. Those pass, where
    ``require_seated_agent`` would ask the project roster, which seats no agent.
    """
    claims = scoped_token_claims(token)
    if claims is None:
        raise AuthenticationRequiredError("Agent credential is invalid or expired")
    if claims.get("t"):
        await require_seated_agent(session, token, project_id=project_id, topic_id=None)
