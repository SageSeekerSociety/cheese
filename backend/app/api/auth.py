"""Trust-boundary wiring: resolve the actor + authorize topic access.

This is where the pure seams (``app.domain.identity.actor``,
``app.domain.authz.policy``) meet the real request — headers, DB, WebSocket. It
composes the injected adapters once; routes depend on ``ActorResolver`` and call
``resolve(...)`` (replacing every ``body.get("author")`` trust-read) and, on
authenticated writes, ``authorize_topic(...)``.
"""

import uuid
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.errors import ForbiddenError
from app.core.obs import get_logger
from app.core.sandbox_auth import verify_scoped_token
from app.core.tokens import verify_session_token
from app.domain.authz.policy import authorize_topic_access
from app.domain.identity.actor import Actor, TokenIdentity, resolve_actor
from app.domain.identity.services import CHEESE_HANDLE, IdentityService
from app.domain.membership.repositories import MemberRepository
from app.domain.project.repositories import ProjectRepository
from app.domain.topic.models import TopicRole
from app.domain.topic.repositories import TopicRepository
from app.domain.topic_membership.repositories import TopicMembershipRepository

_log = get_logger("cheesex.auth")


def _bearer(header: str | None) -> str | None:
    """Extract the token from an ``Authorization: Bearer <token>`` header."""
    if not header:
        return None
    parts = header.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def _token_verifier(token: str) -> TokenIdentity | None:
    claims = verify_session_token(token)
    if claims is None:
        return None
    uid: uuid.UUID | None = None
    if claims["uid"]:
        try:
            uid = uuid.UUID(claims["uid"])
        except ValueError:
            uid = None
    return TokenIdentity(handle=claims["sub"], user_id=uid)


class ActorResolver:
    """Per-request resolver. Reads the human bearer token + agent scoped token,
    then resolves/authorizes against the DB."""

    def __init__(self, *, session: AsyncSession, bearer: str | None, cheese_token: str):
        self._session = session
        self._bearer = bearer
        self._cheese_token = cheese_token
        self._identity = IdentityService(session)

    async def resolve(
        self,
        *,
        fallback_handle: str | None,
        topic_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
    ) -> Actor:
        """Resolve the acting identity (token > cheese > handle fallback). Raises
        nothing — an anonymous fallback resolves to a plain ``anonymous`` actor so
        existing callers that omitted an author keep working."""

        async def cheese_valid() -> bool:
            # Only a SCOPED per-turn token identifies "the agent is acting" and
            # binds it to this project/topic. The bare global SANDBOX_TOKEN is a
            # gate-only dev override (sandbox_auth) — it opens the write-surface
            # but must NOT hijack authorship, so it is deliberately not honored
            # here (cheese-gated routes set author="cheese" themselves).
            if not self._cheese_token:
                return False
            return verify_scoped_token(
                self._cheese_token,
                project_id=str(project_id) if project_id else None,
                topic_id=str(topic_id) if topic_id else None,
            )

        actor = await resolve_actor(
            bearer_token=self._bearer,
            verify_token=_token_verifier,
            cheese_valid=cheese_valid,
            is_agent=self._identity.is_agent,
            cheese_handle=CHEESE_HANDLE,
            fallback_handle=fallback_handle,
        )
        if actor is None:
            actor = Actor(
                handle="anonymous", user_id=None, is_agent=False, via="handle"
            )
        if actor.via == "handle" and actor.handle != "anonymous":
            _log.info("actor_handle_fallback", handle=actor.handle)
        return actor

    async def authorize_topic(
        self, actor: Actor, *, project_id: uuid.UUID, topic_id: uuid.UUID
    ) -> None:
        """Enforce topic access for authenticated actors (no-op for the handle
        fallback / agents). Raises ForbiddenError on an outsider."""
        if not settings.authz_enforce_topic_access:
            return
        members = TopicMembershipRepository(self._session)
        project_members = MemberRepository(self._session)
        projects = ProjectRepository(self._session)

        async def topic_role(tid: uuid.UUID, handle: str) -> TopicRole | None:
            row = await members.get(topic_id=tid, member_handle=handle)
            return row.role if row is not None else None

        async def roster_exists(tid: uuid.UUID) -> bool:
            return await members.count_for_topic(tid) > 0

        async def is_project_member(pid: uuid.UUID, handle: str) -> bool:
            if await project_members.get(project_id=pid, user_handle=handle):
                return True
            project = await projects.get(pid)
            return project is not None and project.owner_handle == handle

        allowed = await authorize_topic_access(
            actor,
            project_id=project_id,
            topic_id=topic_id,
            topic_role=topic_role,
            roster_exists=roster_exists,
            is_project_member=is_project_member,
        )
        if not allowed:
            _log.info(
                "topic_access_denied", handle=actor.handle, topic=str(topic_id)
            )
            raise ForbiddenError("你不是这个话题的成员，无权在此操作")

    async def project_of_topic(self, topic_id: uuid.UUID) -> uuid.UUID | None:
        topic = await TopicRepository(self._session).get(topic_id)
        return topic.project_id if topic is not None else None


def get_actor_resolver(
    request: Request, db: Annotated[AsyncSession, Depends(get_db)]
) -> ActorResolver:
    return ActorResolver(
        session=db,
        bearer=_bearer(request.headers.get("authorization")),
        cheese_token=request.headers.get("x-cheese-token") or "",
    )


# Browsers cannot set an Authorization header on a WebSocket, so the chat route
# builds the resolver from the ?token= query param itself (照 reference
# viewer_authz.py) — see app/api/routes/chat.py.
ActorResolverDep = Annotated[ActorResolver, Depends(get_actor_resolver)]
