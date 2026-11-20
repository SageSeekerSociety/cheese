"""门面：`app.api.auth` 仍是既有调用点与路由依赖的导入路径。

拆开的起因只是长度：`ActorResolver` 连它的授权方法搬去了
:mod:`app.api.auth.resolver`。接线留在这一层 —— 路由 `Depends` 的
``ActorResolverDep`` / ``MediaActorResolverDep``、media cookie 和
``require_seated_agent``。

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
from fastapi.requests import HTTPConnection
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth.resolver import ActorResolver as ActorResolver
from app.common.auth import verify_access_token
from app.core import credentials
from app.core.config import GATEWAY_MOUNT, settings
from app.core.credentials import Kind
from app.core.db import get_db
from app.core.errors import (
    AuthenticationRequiredError,
    ForbiddenError,
    NotFoundError,
)
from app.core.sandbox_auth import held_by_a_machine, scoped_token_claims
from app.core.sentences import say
from app.domain.conversation import services as conversations
from app.domain.identity.actor import TokenIdentity
from app.domain.topic_membership.services import (
    TopicMemberService as TopicMemberService,
)
from app.domain.user.repositories import UserRepository as UserRepository


def _token_verifier(token: str) -> TokenIdentity | None:
    claims = verify_access_token(token)
    if claims is None:
        return None
    # The user id is kept alongside the handle: device binding
    # (device.owner_user_id, an int column) needs it.
    return TokenIdentity(handle=claims.handle, user_id=claims.user_id)


#: The kinds of credential an agent acts with, rather than a person's own.
_AGENT_KINDS = frozenset(
    {
        Kind.AGENT,
        Kind.PROJECT_AGENT,
        Kind.PERSONAL,
        Kind.PERSONAL_EXECUTION,
        Kind.DELEGATED,
    }
)


def resolver_for(request: Request, db: AsyncSession, token: str) -> ActorResolver:
    """The resolver for a request presenting ``token``: one of the credentials
    the platform hands its agents, by the kind it says it is
    (`app.core.credentials`), or else a person's sign-in — one that does not
    verify included, which is told to sign in again."""
    person = not (
        credentials.kind_of(token) in _AGENT_KINDS
        or credentials.is_platform_secret(token)
    )
    return ActorResolver(
        session=db,
        bearer=token if person else None,
        cheese_token="" if person else token,
        writes=request.method not in ("GET", "HEAD", "OPTIONS"),
    )


def presented_credential(connection: HTTPConnection) -> str:
    """The credential this request carries (`credentials.presented`), for a
    route that reads a credential itself rather than resolving a caller."""
    return credentials.presented(connection.headers)


#: The credential a request carries, as a route parameter.
PresentedCredential = Annotated[str, Depends(presented_credential)]


def get_actor_resolver(
    request: Request, db: Annotated[AsyncSession, Depends(get_db)]
) -> ActorResolver:
    return resolver_for(request, db, credentials.presented(request.headers))


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

    Only a person's token sent as ``Authorization`` is mirrored: a cookie is
    never turned into a fresh cookie, so it cannot outlive the page's own token.
    """
    token = credentials.presented(request.headers)
    if credentials.kind_of(token) is Kind.ACCESS:
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
    token = credentials.presented(request.headers)
    return resolver_for(request, db, token or request.cookies.get(MEDIA_COOKIE, ""))


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
    if not claims or held_by_a_machine(claims) or claims.get("t") != str(topic_id):
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
    allow_machine: bool = False,
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
    if claims is None or (not allow_machine and held_by_a_machine(claims)):
        raise AuthenticationRequiredError("Agent credential is invalid or expired")
    if not claims.get("a") and not claims.get("t"):
        return
    # The room this request acts in; without one (a project-wide route), the
    # room the credential was minted in. An agent holds no project-roster
    # grant, so its seat in that room is the only standing it has.
    room = topic_id
    if room is None and claims.get("t"):
        room = uuid.UUID(claims["t"])
    # The seat is asked by handle. The user id an int-keyed row would need is
    # not asked for, so the user row that holds it is not read: every model
    # admission comes through here.
    resolver = ActorResolver(
        session=session,
        bearer=None,
        cheese_token=token,
        allow_machine=allow_machine,
        bind_user_id=False,
    )
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
