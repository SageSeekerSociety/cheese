"""Trust-boundary wiring: resolve the actor + authorize topic access.

This is where the pure seams (``app.domain.identity.actor``,
``app.domain.authz.policy``) meet the real request — headers, DB, WebSocket. It
composes the injected adapters once; routes depend on ``ActorResolver`` and call
``resolve(...)`` (replacing every ``body.get("author")`` trust-read) and, on
authenticated writes, ``authorize_topic(...)``.
"""

import uuid
from dataclasses import replace
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.errors import AuthenticationRequiredError, ForbiddenError
from app.core.obs import get_logger
from app.core.sandbox_auth import (
    looks_like_project_agent_credential,
    project_agent_claims,
    scoped_token_claims,
    token_agent_handle,
    verify_scoped_token,
)
from app.core.tokens import verify_session_token
from app.domain.agent.device_attribution import resolve_screen_actor
from app.domain.agent.device_hub import device_hub
from app.domain.agent_credential.services import ProjectAgentCredentialService
from app.domain.authz.policy import authorize_topic_access
from app.domain.identity.actor import Actor, TokenIdentity, resolve_actor
from app.domain.identity.services import CHEESE_HANDLE, IdentityService
from app.domain.membership.repositories import MemberRepository
from app.domain.project.repositories import ProjectRepository
from app.domain.topic.models import TopicRole
from app.domain.topic.repositories import TopicRepository
from app.domain.topic_membership.repositories import TopicMembershipRepository
from app.domain.user.repositories import UserRepository

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
    # fusion unify P3: main-minted tokens carry the username in ``handle`` and the
    # int User PK in ``sub``; legacy cheesex-minted ones put the handle in ``sub``
    # (non-numeric) and have no int id. Resolve user_id from ``sub`` when it's the
    # int id — device binding (device.owner_user_id, an int column) needs it.
    sub = claims["sub"]
    user_id: int | None = int(sub) if sub and sub.isdigit() else None
    # Prefer the explicit handle claim so ONE token authenticates both API layers.
    return TokenIdentity(handle=claims["handle"] or sub, user_id=user_id)


class ActorResolver:
    """Per-request resolver. Reads the human bearer token + agent scoped token,
    then resolves/authorizes against the DB."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        bearer: str | None,
        cheese_token: str,
        screen_token: str = "",
    ):
        self._session = session
        self._bearer = bearer
        self._cheese_token = cheese_token
        # A ``cheese`` call made from inside a self-hosted device screen carries that
        # screen's token (``X-Cheese-Screen``). It makes the call act as the screen's
        # agent-user (device agent-as-user, P3), not the platform 芝士 — see resolve().
        self._screen_token = screen_token
        self._identity = IdentityService(session)
        self._credentials = ProjectAgentCredentialService(session)

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

        # A scoped token that is genuinely valid but minted for ANOTHER topic /
        # project is a scope violation, not "no credential". It used to fall
        # through to the Phase-0 handle fallback and resolve to ``anonymous`` —
        # which policy.py treats as UNauthenticated and therefore lets through,
        # so presenting the wrong token beat presenting none and the write landed
        # with its author erased. Observed live: a parent topic posting a comment
        # into a child topic, recorded as ``anonymous``.
        self._reject_out_of_scope_token(topic_id=topic_id, project_id=project_id)

        # A project agent credential names a project and reaches every topic in
        # it, so the project this request acts on is what it must be judged
        # against — via the topic when the route named only that. Resolved once,
        # and only when such a credential is actually presented, so ordinary
        # traffic pays for neither the parse nor the extra read.
        credential_project: uuid.UUID | None = None
        if looks_like_project_agent_credential(self._cheese_token):
            credential_project = project_id or (
                await self.project_of_topic(topic_id) if topic_id else None
            )
            self._reject_out_of_scope_credential(credential_project)

        async def cheese_valid() -> bool:
            # Only a SCOPED per-turn token identifies "the agent is acting" and
            # binds it to this project/topic. The bare global SANDBOX_TOKEN is a
            # gate-only dev override (sandbox_auth) — it opens the write-surface
            # but must NOT hijack authorship, so it is deliberately not honored
            # here (cheese-gated routes set author="cheese" themselves).
            if not self._cheese_token:
                return False
            # A project agent credential is the same claim widened: 芝士 acting,
            # bound to a project instead of to one turn's topic. It authenticates
            # wherever the request's project matches it, and nowhere else — a
            # request with no project context (credential_project is None) fails
            # closed rather than falling back to a broader grant.
            if looks_like_project_agent_credential(self._cheese_token):
                if credential_project is None:
                    return False
                return await self._credentials.authenticate(
                    self._cheese_token, project_id=credential_project
                )
            return verify_scoped_token(
                self._cheese_token,
                project_id=str(project_id) if project_id else None,
                topic_id=str(topic_id) if topic_id else None,
            )

        # WHO the scoped token acts as: the topic's own 分身 (claim ``a``), not the
        # one collapsed platform account. A token minted before this claim existed —
        # or a project-wide one with no topic — carries none and falls back to
        # ``cheese``, which is exactly the previous behaviour.
        agent_handle = (
            token_agent_handle(self._cheese_token) if self._cheese_token else None
        )
        actor = await resolve_actor(
            bearer_token=self._bearer,
            verify_token=_token_verifier,
            cheese_valid=cheese_valid,
            is_agent=self._identity.is_agent,
            cheese_handle=agent_handle or CHEESE_HANDLE,
            fallback_handle=fallback_handle,
        )
        if actor is None:
            actor = Actor(
                handle="anonymous", user_id=None, is_agent=False, via="handle"
            )
        actor = await self._recover_numeric_handle(actor)
        # Device-screen attribution (P3): a cheese call from inside an enrolled device's
        # screen carries that screen's token. It is a per-screen capability that proves
        # the call runs as that screen's agent — so it acts as the device agent-user
        # (agent-as-user), overriding the generic cheese identity. The write-surface
        # gate (cheese_token_gate) is unaffected; this only decides *who* the actor is.
        if self._screen_token:
            screen = resolve_screen_actor(device_hub, self._screen_token)
            if screen is not None:
                return Actor(
                    handle=screen.agent_handle,
                    user_id=screen.agent_user_id,
                    is_agent=True,
                    via="cheese",
                )
        if actor.via == "handle" and actor.handle != "anonymous":
            _log.info("actor_handle_fallback", handle=actor.handle)
        return actor

    async def resolve_recipient(
        self,
        *,
        requested: str | None,
        project_id: uuid.UUID | None = None,
        allow_anonymous: bool = True,
    ) -> str:
        """Whose per-person mailbox (notifications, badges, read-state) this
        request addresses. Shared by every per-recipient endpoint so the rule
        lives at the trust boundary, not in a route-local helper.

        A recipient is an identity, and identity never comes from a query
        parameter or body field — the requested handle is only an assertion to
        check against the verified credential:

        - verified caller naming nobody, or naming themselves → their mailbox;
        - verified caller naming someone else → 403, never a silent redirect;
        - a presented credential that does not verify (malformed or expired
          token) → 401 — downgrading a failed credential to ``anonymous`` is
          the bug class that let stripped headers read anyone's mail;
        - no credential at all + a named handle → 401: the Phase-0 handle
          fallback exists for authorship convenience and must never grant a
          mailbox, or naming ``?target_handle=bob`` would read (and clear)
          bob's mail for free;
        - no credential, nobody named → the ``anonymous`` broadcast-only slice
          when the endpoint allows it (reads), else 401 (writes).
        """
        wanted = (requested or "").strip() or None
        actor = await self.resolve(fallback_handle=None, project_id=project_id)
        if actor.authenticated:
            if wanted is not None and wanted != actor.handle:
                raise ForbiddenError("不能查看或操作别人的通知")
            return actor.handle
        if self._bearer:
            raise AuthenticationRequiredError("登录状态无效或已过期，请重新登录")
        if wanted is not None or not allow_anonymous:
            raise AuthenticationRequiredError("访问个人通知需要先登录")
        return "anonymous"

    def _reject_out_of_scope_token(
        self, *, topic_id: uuid.UUID | None, project_id: uuid.UUID | None
    ) -> None:
        """403 when the presented scoped token names a different resource.

        Deliberately narrow — it fires ONLY for a well-formed, correctly-signed,
        unexpired scoped token. An absent header, a malformed or expired token,
        and the global ``SANDBOX_TOKEN`` dev override all keep their existing
        behaviour (``scoped_token_claims`` returns None for each), so this closes
        the identity-collapse path without touching the dev/legacy surface.

        A token with no ``t`` claim (project-wide capability: git-http, LLM proxy)
        is not out of scope for a topic route — it simply does not authenticate
        there, which ``cheese_valid`` already handles.
        """
        if not self._cheese_token:
            return
        claims = scoped_token_claims(self._cheese_token)
        if claims is None:
            return
        if project_id is not None and claims.get("p") != str(project_id):
            _log.info("token_scope_violation", kind="project", got=claims.get("p"))
            raise ForbiddenError("这个 token 属于别的项目，不能在这里操作")
        claimed_topic = claims.get("t")
        if (
            topic_id is not None
            and claimed_topic is not None
            and claimed_topic != str(topic_id)
        ):
            _log.info("token_scope_violation", kind="topic", got=claimed_topic)
            raise ForbiddenError("这个 token 属于别的话题，不能在这里操作")

    def _reject_out_of_scope_credential(self, target: uuid.UUID | None) -> None:
        """403 when a valid project agent credential names a DIFFERENT project.

        Same reasoning as ``_reject_out_of_scope_token``: silently failing to
        authenticate would drop the caller into the Phase-0 handle fallback,
        which policy.py reads as unauthenticated and lets through — so a
        credential for another project would beat presenting none at all. It
        fires only on a well-formed, correctly-signed, unexpired credential; a
        revoked or malformed one is simply not a credential (and a revoked one
        must not be told apart from a forged one here).
        """
        claims = project_agent_claims(self._cheese_token)
        if claims is None or target is None:
            return
        if claims.project_id != str(target):
            _log.info("credential_scope_violation", got=claims.project_id)
            raise ForbiddenError("这个凭证属于别的项目，不能在这里操作")

    async def _recover_numeric_handle(self, actor: Actor) -> Actor:
        """Repair a token actor whose handle degraded into the int User PK.

        ``_token_verifier`` falls back to the ``sub`` claim when a main-minted
        token carries no ``handle``; ``sub`` is the int PK, so the actor ends up
        named e.g. ``"470"``. Every authorization key in the platform is the
        handle STRING (``topic_memberships.member_handle``, ``member.user_handle``,
        ``project.owner_handle``) — a numeric handle therefore matches no roster
        and no project membership, and the caller silently loses every permission
        they actually hold. Resolve the real username from the id instead.

        A user we cannot resolve keeps the numeric handle (authorization still
        denies, as it must) but is logged loudly — the previous behaviour failed
        silently, which is what made this class of bug so hard to trace.
        """
        if actor.via != "token" or actor.user_id is None:
            return actor
        if not actor.handle.isdigit():
            return actor
        user = await UserRepository(self._session).get_by_id(actor.user_id)
        if user is None:
            _log.warning("token_handle_unresolved", user_id=actor.user_id)
            return actor
        _log.info("token_handle_recovered", user_id=actor.user_id, handle=user.username)
        return replace(actor, handle=user.username)

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
            _log.info("topic_access_denied", handle=actor.handle, topic=str(topic_id))
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
        screen_token=request.headers.get("x-cheese-screen") or "",
    )


# Browsers cannot set an Authorization header on a WebSocket, so the chat route
# builds the resolver from the ?token= query param itself (照 reference
# viewer_authz.py) — see app/api/routes/chat.py.
ActorResolverDep = Annotated[ActorResolver, Depends(get_actor_resolver)]
