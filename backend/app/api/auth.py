"""Trust-boundary wiring: resolve the actor + authorize topic access.

This is where the pure seams (``app.domain.identity.actor``,
``app.domain.authz.policy``) meet the real request — headers, DB, WebSocket. It
composes the injected adapters once; routes depend on ``ActorResolver`` and call
``resolve(...)`` (replacing every ``body.get("author")`` trust-read) and, on
authenticated writes, ``authorize_topic(...)``.
"""

import uuid
from collections.abc import Sequence
from dataclasses import replace
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.project_access import may_read_project, outsiders_reading
from app.common.auth import verify_access_token
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import (
    AuthenticationRequiredError,
    ForbiddenError,
    NotFoundError,
)
from app.core.obs import get_logger
from app.core.sandbox_auth import (
    DelegatedClaims,
    delegated_claims,
    is_global_sandbox_token,
    looks_like_delegated_credential,
    looks_like_project_agent_credential,
    project_agent_claims,
    scoped_token_claims,
    token_agent_handle,
    verify_scoped_token,
)
from app.core.sentences import say
from app.domain.agent.device_attribution import resolve_screen_actor
from app.domain.agent.device_hub import device_hub
from app.domain.agent_credential.services import ProjectAgentCredentialService
from app.domain.authz.policy import authorize_topic_access
from app.domain.conversation import services as conversations
from app.domain.identity.actor import Actor, TokenIdentity, resolve_actor
from app.domain.identity.handles import UNRESOLVED_AGENT_HANDLE
from app.domain.project.repositories import ProjectRepository
from app.domain.project.services import refuse_writes_if_archived
from app.domain.task.repositories import TaskRepository
from app.domain.task.visibility_service import TaskVisibilityService
from app.domain.team.repositories import TeamRepository
from app.domain.topic.models import Topic, TopicRole
from app.domain.topic.repositories import TopicRepository
from app.domain.topic_membership.repositories import TopicMembershipRepository
from app.domain.topic_membership.services import TopicMemberService
from app.domain.user.repositories import UserRepository

_log = get_logger("cheesex.auth")

#: The routes a delegated credential opens (`sandbox_auth.DelegatedClaims`), by
#: endpoint, and whether each changes something. Every other route refuses it:
#: a 芝士 answering for someone acts through these and nothing else, so a new
#: tool for it is a new line here, read by whoever reviews it.
DELEGATED_ROUTES: dict[str, bool] = {
    "app.api.routes.living_docs.get_document": False,
    "app.api.routes.living_docs.edit_doc_passages": True,
    "app.api.routes.project_context.search_project_context": False,
    "app.api.routes.topics_preview.preview_file": False,
    "app.api.routes.memory_files.list_memory_files": False,
    "app.api.routes.docs_site.agent_search_docs": False,
    "app.api.routes.docs_site.agent_read_docs": False,
    "app.api.routes.tasks.participation.list_joined_tasks": False,
}


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


def _private(topic: Topic | None) -> bool:
    """Whether only the room's own seats may read it: a private chat (the one
    place this file reads that flag), or a private channel."""
    return bool(topic and (topic.is_private or topic.members_only))


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
        writes: bool = False,
        endpoint: str | None = None,
    ):
        self._session = session
        # Which route this is (``module.function``), for a delegated credential,
        # which opens only the routes in ``DELEGATED_ROUTES``.
        self._endpoint = endpoint
        # Whether this request changes something. A write that names a project,
        # or a room of one, is refused while that project is archived — here,
        # once, so that no write route can forget to ask (see ``resolve``).
        self._writes = writes
        self._bearer = bearer
        self._cheese_token = cheese_token
        # A ``cheese`` call made from inside a self-hosted device screen carries that
        # screen's token (``X-Cheese-Screen``). It makes the call act as the screen's
        # agent-user (device agent-as-user, P3), not the platform 芝士 — see resolve().
        self._screen_token = screen_token
        self._credentials = ProjectAgentCredentialService(session)

    def credential_conversation(self) -> uuid.UUID | None:
        """The conversation the presented credential was minted in: a room, or
        a task its session works — where what the agent does elsewhere in the
        project is told. None for a person, a project credential, a delegated
        one, or a token naming no conversation."""
        claims = scoped_token_claims(self._cheese_token) if self._cheese_token else None
        named = (claims or {}).get("t")
        return uuid.UUID(named) if named else None

    async def _confine_task_credential(self, topic_id: uuid.UUID | None) -> None:
        """A credential minted for a task's conversation acts in no other
        conversation — not its room, not another task — even the session-wide
        one that otherwise reaches across its project. Routes that name no
        conversation (the project's library, its documents) it reaches as any
        session of the project does."""
        if not self._cheese_token or topic_id is None:
            return
        claims = scoped_token_claims(self._cheese_token)
        named = (claims or {}).get("t")
        if not named or named == str(topic_id):
            return
        if await conversations.is_task(self._session, uuid.UUID(named)):
            raise ForbiddenError("This credential works another conversation")

    def delegation(self) -> DelegatedClaims | None:
        """The question the presented credential acts for, when it is a valid
        delegated one: who asked, which agent answers, under which work."""
        return delegated_claims(self._cheese_token) if self._cheese_token else None

    async def _resolve_delegated(
        self,
        *,
        topic_id: uuid.UUID | None,
        project_id: uuid.UUID | None,
        read_only: bool,
    ) -> Actor:
        """The person a 芝士 answers for, as the actor: what the route allows is
        judged by what that person may do. Refused outright (never anonymous)
        when the credential is not valid, the route is not one it opens, the
        route changes something and the question may only be answered, or the
        route acts somewhere the credential was not minted for."""
        claims = self.delegation()
        if claims is None:
            raise AuthenticationRequiredError(
                "Delegated credential is invalid or expired"
            )
        writes = DELEGATED_ROUTES.get(self._endpoint or "")
        if writes is None:
            raise ForbiddenError("This credential does not open this route")
        if writes and claims.read_only:
            raise ForbiddenError("This question may only be answered, not act")
        if topic_id is not None and project_id is None:
            project_id = await self.project_of_topic(topic_id)
        if claims.project_id is None:
            if topic_id is not None or project_id is not None:
                raise ForbiddenError(say("tokenOtherProject"))
        elif project_id is None and topic_id is None:
            # Minted for a room, and this route names none to hold it to.
            raise ForbiddenError("This credential is restricted to one room")
        else:
            if project_id is not None and str(project_id) != claims.project_id:
                raise ForbiddenError(say("tokenOtherProject"))
            if (
                topic_id is not None
                and claims.topic_id is not None
                and str(topic_id) != claims.topic_id
            ):
                raise ForbiddenError(say("tokenOtherTopic"))
        if not read_only:
            await self._refuse_archived_write(project_id=project_id, topic_id=topic_id)
        return Actor(handle=claims.handle, user_id=claims.user_id, via="delegated")

    def credential_agent(self) -> str | None:
        """The teammate the presented cheese credential itself names (its ``a``
        claim), or None. Read off the credential rather than off the resolved
        actor, for a caller that has to match what ANOTHER connection holding
        the same credential presents — the preview tunnel is keyed by exactly
        this claim."""
        return token_agent_handle(self._cheese_token) if self._cheese_token else None

    def on_the_dev_credential(self, actor: Actor) -> bool:
        """Was the bare global sandbox token the ONLY thing that admitted this
        request — nobody resolved, no roster to ask?

        That token is the trusted-single-host override: ``authorize_topic`` and
        ``authorize_project`` let it through without resolving anybody, so a
        route just admitted on it has no participant whose seat could be
        checked. The ``actor`` half matters: callers send it on EVERY request in
        dev (a test client, the CLI), so its presence alone says nothing — what
        is asked here is whether it is also all there was.
        """
        return not actor.authenticated and is_global_sandbox_token(self._cheese_token)

    async def resolve(
        self,
        *,
        topic_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        read_only: bool = False,
    ) -> Actor:
        """Resolve the verified identity, rejecting invalid agent credentials.

        A missing credential produces the anonymous actor; it grants no room
        or project access. Nothing in the request body or query names the
        caller.

        On a write that names a project or a room, an archived project is
        refused with ``ProjectArchivedError``: every project-scoped write route
        resolves its caller against the project it acts on, so this is the one
        place that covers them all, including the ones added later. A POST that
        only reads (it mints a viewing grant) passes ``read_only``.
        """

        if looks_like_delegated_credential(self._cheese_token):
            return await self._resolve_delegated(
                topic_id=topic_id, project_id=project_id, read_only=read_only
            )

        # A scoped token that is genuinely valid but minted for ANOTHER topic /
        # project is a scope violation, not "no credential". It used to fall
        # through and resolve to ``anonymous`` —
        # which policy.py treats as UNauthenticated and therefore lets through,
        # so presenting the wrong token beat presenting none and the write landed
        # with its author erased. Observed live: a parent topic posting a comment
        # into a child topic, recorded as ``anonymous``.
        if self._cheese_token and topic_id is not None and project_id is None:
            project_id = await self.project_of_topic(topic_id)
        self._reject_out_of_scope_token(topic_id=topic_id, project_id=project_id)
        await self._confine_task_credential(topic_id)

        # A project credential is bounded by a project, so the project this
        # request acts on is what it must be judged
        # against — via the topic when the route named only that. Resolved once,
        # and only when such a credential is actually presented, so ordinary
        # traffic pays for neither the parse nor the extra read.
        credential_project: uuid.UUID | None = None
        if looks_like_project_agent_credential(self._cheese_token):
            credential_project = project_id or (
                await self.project_of_topic(topic_id) if topic_id else None
            )
            self._reject_out_of_scope_credential(credential_project)

        if not read_only:
            await self._refuse_archived_write(project_id=project_id, topic_id=topic_id)

        async def cheese_valid() -> bool:
            # Only a SCOPED per-turn token identifies "the agent is acting" and
            # binds it to this project/topic. The bare global SANDBOX_TOKEN is a
            # gate-only dev override (sandbox_auth) — it opens the write-surface
            # but must NOT hijack authorship, so it is deliberately not honored
            # here (cheese-gated routes set author="cheese" themselves).
            if not self._cheese_token:
                return False
            if project_id is None:
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
            claims = scoped_token_claims(self._cheese_token)
            # A project-wide capability (git-http, the LLM proxies) is not a
            # participant speaking: it names no agent and has no room whose
            # roster could say which one is. A per-turn token scoped to a room
            # does have one, so it authenticates whether or not it pinned a
            # teammate — `acting_agent` below reads who from the seat.
            if not token_agent_handle(self._cheese_token) and not (
                claims and claims.get("t")
            ):
                return False
            return verify_scoped_token(
                self._cheese_token,
                project_id=str(project_id) if project_id else None,
                topic_id=(
                    str(topic_id)
                    if topic_id and not (claims and claims.get("s") == "project")
                    else None
                ),
            )

        # The credential names a fixed participant. A project credential uses
        # the project's own 芝士, never the agent of the destination room.
        agent_handle = (
            token_agent_handle(self._cheese_token) if self._cheese_token else None
        )
        if credential_project is not None:
            agent_handle = await self._credentials.agent_handle(credential_project)
        elif (
            topic_id is not None
            and self._cheese_token
            and not is_global_sandbox_token(self._cheese_token)
        ):
            agent_handle = await self.acting_agent(topic_id, agent_handle)
        actor = await resolve_actor(
            bearer_token=self._bearer,
            verify_token=_token_verifier,
            cheese_valid=cheese_valid,
            cheese_handle=agent_handle or UNRESOLVED_AGENT_HANDLE,
        )
        if actor is None:
            actor = Actor(handle="anonymous", user_id=None, via="anonymous")
        if (
            self._cheese_token
            and not is_global_sandbox_token(self._cheese_token)
            and (not actor.authenticated or actor.handle == UNRESOLVED_AGENT_HANDLE)
        ):
            # A live scoped token is bound to a project, so a route that names
            # neither a project nor a room gives it nothing to authenticate
            # against. Saying "invalid or expired" there sends the agent off to
            # distrust a credential that works everywhere it is meant to.
            if (
                topic_id is None
                and project_id is None
                and scoped_token_claims(self._cheese_token) is not None
            ):
                raise ForbiddenError(
                    "This credential is restricted to one project; "
                    "call a route that names the project or room"
                )
            raise AuthenticationRequiredError("Agent credential is invalid or expired")
        actor = await self._recover_numeric_handle(actor)
        # An authenticated actor whose credential carried no int PK: look the
        # row up by handle, because int-keyed rows (device.owner_user_id) cannot
        # be bound without it. Asked of every such actor rather than only of the
        # agents — the caller's KIND was never what made the id missing (a
        # legacy cheesex token puts the handle in ``sub`` and carries no id
        # either), so branching on it just left those callers unbound.
        if actor.authenticated and actor.user_id is None:
            user = await UserRepository(self._session).get_by_username(actor.handle)
            if user is not None:
                actor = replace(actor, user_id=user.id)
        # Device-screen attribution (P3): a cheese call from inside an enrolled device's
        # screen carries that screen's token. It is a per-screen capability that proves
        # the call runs as that screen's agent — so it acts as the device agent-user
        # (agent-as-user), overriding the generic cheese identity. The write-access
        # declarations (app/api/write_access.py) are unaffected; this only decides
        # *who* the actor is.
        if self._screen_token:
            screen = resolve_screen_actor(device_hub, self._screen_token)
            if screen is not None:
                return Actor(
                    handle=screen.agent_handle,
                    user_id=screen.agent_user_id,
                    via="cheese",
                )
        return actor

    async def _refuse_archived_write(
        self, *, project_id: uuid.UUID | None, topic_id: uuid.UUID | None
    ) -> None:
        """A write to an archived project stops here. Asked by ``resolve`` and
        again by the two ``authorize_*`` doors, because a route may resolve its
        caller without naming the project and only name it when it authorizes."""
        if not self._writes:
            return
        target = project_id or (
            await self.project_of_topic(topic_id) if topic_id else None
        )
        if target is not None:
            await refuse_writes_if_archived(self._session, target)

    async def acting_agent(self, topic_id: uuid.UUID, handle: str | None) -> str:
        """Who a per-turn credential in this room acts as.

        A credential that names an agent acts as that one: a handle belongs to
        the agent it was minted from, and no room may rename it. A credential
        that names nobody is a turn in this room without a teammate pinned, and
        the room's ROSTER says who answers it — the project's default when it is
        seated, else the first agent there. The room's id never says: a room may
        seat several agents, so deriving a name from it would give two of them
        the same one and the same agent two names in two rooms.
        """
        if handle:
            return handle
        return await TopicMemberService(self._session).resolve_agent_handle(
            await conversations.room_of(self._session, topic_id)
        )

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
        - no credential at all + a named handle → 401: naming
          ``?target_handle=bob`` must never read (and clear) bob's mail for
          free;
        - no credential, nobody named → the ``anonymous`` handle when the
          endpoint allows it (reads), else 401 (writes). That handle is on
          nobody's roster, so it addresses an empty mailbox: a notification is
          addressed to one person, broadcasts included (they expand to a row
          per person on the roster when written), and an unidentified caller
          holds none of those rows.
        """
        wanted = (requested or "").strip() or None
        # A mailbox is its owner's, not the project's: clearing what came from
        # an archived project is still yours to do.
        actor = await self.resolve(project_id=project_id, read_only=True)
        if actor.authenticated:
            if wanted is not None and wanted != actor.handle:
                raise ForbiddenError(say("notificationsNotYours"))
            return actor.handle
        if self._bearer:
            raise AuthenticationRequiredError(say("sessionExpired"))
        if wanted is not None or not allow_anonymous:
            raise AuthenticationRequiredError(say("notificationsSignIn"))
        return "anonymous"

    def reject_failed_credential(self, actor: Actor) -> None:
        """401 when a bearer WAS presented and did not verify — as opposed to
        no bearer at all, which stays anonymous.

        `resolve()` deliberately never raises: it answers "who is this" and
        「认不出」 is a legitimate answer for the surfaces that serve anonymous
        readers. But on a route whose meaning is per-caller, collapsing a
        *failed* credential into 「没有凭据」 makes the response a lie the client
        cannot detect — it gets a 200 with an honest-looking empty payload and
        no reason to go get a working token. That is the second half of the
        「左边栏冒出一堆不是我的项目」 bug: #323 stopped the listing from leaking
        other people's projects, but the lapsed-token user was left staring at
        an empty sidebar with nothing to react to.

        `resolve_recipient` and `require_verified_caller` already draw exactly
        this line for mailboxes and gated writes ("downgrading a failed
        credential to ``anonymous`` is the bug class that let stripped headers
        read anyone's mail"). This is the same rule, spelled once, for reads.

        Note what it does NOT do: with no ``Authorization`` header there is no
        failed credential, so genuinely anonymous traffic is untouched, and an
        agent authenticated by its scoped token is authenticated regardless of
        what a stale bearer alongside it says.
        """
        if not actor.authenticated and self._bearer:
            raise AuthenticationRequiredError(say("sessionExpired"))

    async def require_verified_caller(
        self, *, project_id: uuid.UUID | None = None, topic_id: uuid.UUID | None = None
    ) -> Actor:
        """Some verified credential must open a gated write — a session token,
        the agent's scoped token, or the global sandbox override — else 401.

        For write endpoints that take no per-person target but must not be an
        anonymous drive-by surface. A cheese-token middleware gate matching
        path regexes used to be the only thing standing in front of notification
        creation — a gate in another layer is a gate a refactor (or a path the
        regex does not cover) can silently drop, so the route enforces it itself. The global ``SANDBOX_TOKEN`` stays gate-only
        (dev / trusted-single-host override): it opens the surface but never
        becomes an identity — same rule as ``resolve()``.
        """
        actor = await self.resolve(project_id=project_id, topic_id=topic_id)
        if actor.authenticated:
            if project_id is None and topic_id is not None:
                project_id = await self.project_of_topic(topic_id)
            await self.refuse_unseated_agent(actor, project_id=project_id)
            return actor
        if self._bearer:
            raise AuthenticationRequiredError(say("sessionExpired"))
        if is_global_sandbox_token(self._cheese_token):
            return actor
        raise AuthenticationRequiredError(say("sandboxTokenRequired"))

    async def refuse_unseated_agent(
        self, actor: Actor, *, project_id: uuid.UUID | None
    ) -> None:
        """Refuse an agent's scoped credential once the agent is off the room it
        was minted in (``require_seated_in_its_room``). ``resolve`` checks only
        the signature, and a session credential outlives the agent's seat."""
        if (
            actor.via != "cheese"
            or project_id is None
            or scoped_token_claims(self._cheese_token) is None
        ):
            return
        await require_seated_in_its_room(
            self._session, self._cheese_token, project_id=project_id
        )

    def speaks_for_this_rooms_turn(self, topic_id: uuid.UUID) -> bool:
        """这张凭据就是**这个房间这一轮**的那张令牌吗。

        `Actor.via == "cheese"` 答的是另一个问题 —— 「说话的是不是一个 agent」。
        项目级的 agent 凭据也是 `cheese`，而它够得着这个项目的每一个房间
        （`app.api.write_access.WriteAccess` 上那句话），全局的 sandbox token 更是谁
        都不是。要「正在这个房间里跑的那一轮」，只能认每一轮现铸的那张 scoped
        token：它把房间签在 `t` 上，冒不出来，也借不到别的房间去用。

        `s == "project"` 那一档不算：它带着一个起始房间，可它要的正是跨房间的通
        行，所以它不是「这个房间这一轮」。
        """
        if not self._cheese_token:
            return False
        claims = scoped_token_claims(self._cheese_token)
        if claims is None or claims.get("s") == "project":
            return False
        return claims.get("t") == str(topic_id)

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
            raise ForbiddenError(say("tokenOtherProject"))
        claimed_topic = claims.get("t")
        if (
            claimed_topic is not None
            and topic_id is None
            and claims.get("s") != "project"
        ):
            raise ForbiddenError("This credential is restricted to one room")
        if (
            topic_id is not None
            and claimed_topic is not None
            and claimed_topic != str(topic_id)
            and claims.get("s") != "project"
        ):
            _log.info("token_scope_violation", kind="topic", got=claimed_topic)
            raise ForbiddenError(say("tokenOtherTopic"))

    def _reject_out_of_scope_credential(self, target: uuid.UUID | None) -> None:
        """403 when a valid project agent credential names a DIFFERENT project.

        Same reasoning as ``_reject_out_of_scope_token``: silently failing to
        authenticate would drop the caller to ``anonymous``, which policy.py
        reads as unauthenticated and lets through — so a
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
            raise ForbiddenError(say("credentialOtherProject"))

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
        self,
        actor: Actor,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        enforce: bool = False,
    ) -> None:
        """Require a verified participant with access to this conversation's
        room."""
        topic_id = await conversations.room_of(self._session, topic_id)
        await self._refuse_archived_write(project_id=project_id, topic_id=topic_id)
        if not enforce and not settings.authz_enforce_topic_access:
            return
        self.reject_failed_credential(actor)
        if not actor.authenticated:
            if is_global_sandbox_token(self._cheese_token):
                return  # Trusted development credential; anonymous access stays denied.
            raise AuthenticationRequiredError(say("signInForRoom"))
        if not await self.can_access_topic(
            actor, project_id=project_id, topic_id=topic_id
        ):
            _log.info("topic_access_denied", handle=actor.handle, topic=str(topic_id))
            room = await TopicRepository(self._session).get(topic_id)
            if room is not None and room.members_only:
                # Someone outside a private channel is not told it exists: the
                # answer is the one a channel that was never made gets.
                raise NotFoundError("Topic not found")
            raise ForbiddenError(say("topicMemberOnly"))

    async def can_access_topic(
        self, actor: Actor, *, project_id: uuid.UUID, topic_id: uuid.UUID
    ) -> bool:
        """Check actual membership even on isolated content hosts in dev mode."""
        topic_id = await conversations.room_of(self._session, topic_id)
        topic = await TopicRepository(self._session).get(topic_id)
        rooms = [(topic_id, _private(topic))]
        return topic_id in await self._readable(actor, project_id, rooms)

    async def readable_topic_ids(
        self, actor: Actor, *, project_id: uuid.UUID, topics: Sequence[Topic]
    ) -> set[uuid.UUID]:
        """Which of these rooms of one project ``actor`` may read.

        The same answer ``can_access_topic`` gives room by room, for a caller
        that already holds the rooms: the roster is read once for all of them
        and project membership, a fact about the person and not the room, is
        asked once. Room by room it was a roster read plus up to six membership
        reads per room, a thousand statements for a project of 170 rooms.
        """
        rooms = [(topic.id, _private(topic)) for topic in topics]
        return await self._readable(actor, project_id, rooms)

    async def _readable(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        rooms: list[tuple[uuid.UUID, bool]],
    ) -> set[uuid.UUID]:
        """The rooms ``authorize_topic_access`` lets ``actor`` read, each given
        as (id, private). The rule stays in the policy; this only answers its
        two questions from one roster read and one membership check."""
        if not rooms:
            return set()
        roles = (
            await TopicMembershipRepository(self._session).roles_for_member(
                [room_id for room_id, _ in rooms], actor.handle
            )
            if actor.authenticated
            else {}
        )
        member: bool | None = None

        async def topic_role(tid: uuid.UUID, handle: str) -> TopicRole | None:
            return roles.get(tid) if handle == actor.handle else None

        async def is_project_member(pid: uuid.UUID, handle: str) -> bool:
            nonlocal member
            if member is None:
                member = await self._is_project_member(pid, handle)
            return member

        return {
            room_id
            for room_id, private in rooms
            if await authorize_topic_access(
                actor,
                project_id=project_id,
                topic_id=room_id,
                topic_role=topic_role,
                is_project_member=is_project_member,
                seats_only=private,
            )
        }

    async def topic_admits_handle(
        self, actor: Actor, *, project_id: uuid.UUID, topic_id: uuid.UUID, handle: str
    ) -> bool:
        """Would this room admit ``handle``, whoever that is?

        The caller is not always the person the request is about: a card names a
        reviewer in its body, and whether the room will later let that reviewer
        accept the card is exactly this question — asked at filing time so the
        two ends cannot drift (`app/domain/review/services.py` lays a card only
        to somebody this answers yes about).

        The rule is not restated here. The handle is swapped on the actor and the
        door's own method answers, which keeps ONE read of ``is_private`` in this
        file (``tests/unit/test_is_private_read_points.py`` is a ratchet: a
        second read point is a second declaration of the same fact, and two
        declarations drift). What is taken as given is the credential half only —
        a handle in a body presents nothing, and this asks whether the room would
        admit them if it did.
        """
        if not actor.authenticated:
            return False
        return await self.can_access_topic(
            replace(actor, handle=handle), project_id=project_id, topic_id=topic_id
        )

    async def authorize_project(self, actor: Actor, *, project_id: uuid.UUID) -> None:
        """Require a verified participant with project membership.

        An agent's per-turn credential reads the project from the conversation
        it was minted in instead (``authorize_reading_from``); what it writes
        still needs the membership it does not have.

        A project that is not there is 404, not 403: the id is a UUID and
        answering "you are not a member of it" about a project that does not
        exist is a claim the guard cannot support. Not-found used to be what
        every one of these routes answered, and the two are told apart by
        looking — membership first, so a member's own request pays no extra
        read.
        """
        await self._refuse_archived_write(project_id=project_id, topic_id=None)
        if not settings.authz_enforce_topic_access:
            return
        self.reject_failed_credential(actor)
        if not actor.authenticated:
            if is_global_sandbox_token(self._cheese_token):
                return  # Trusted development credential; anonymous access stays denied.
            raise AuthenticationRequiredError(say("signInForProject"))
        conversation = self.turn_conversation(actor)
        if conversation is not None and not self._writes:
            await self.authorize_reading_from(
                actor, project_id=project_id, conversation_id=conversation
            )
            return
        if await self._is_project_member(project_id, actor.handle):
            return
        if await ProjectRepository(self._session).get(project_id) is None:
            raise NotFoundError(say("projectNotFound"))
        _log.info("project_access_denied", handle=actor.handle, project=str(project_id))
        raise ForbiddenError(say("projectMemberOnly"))

    def turn_conversation(self, actor: Actor) -> uuid.UUID | None:
        """The conversation an agent's per-turn credential was minted in, when
        this request carries one (claims ``a`` and ``t``).

        An agent holds no place on the project's roster: its standing is the
        seat it works from, and the credential names that place, so a request
        does not have to name it again.
        """
        if actor.via != "cheese" or self._screen_token:
            return None
        claims = scoped_token_claims(self._cheese_token)
        if not claims or not claims.get("a") or not claims.get("t"):
            return None
        try:
            return uuid.UUID(claims["t"])
        except ValueError:
            return None

    async def authorize_reading_from(
        self, actor: Actor, *, project_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> None:
        """Let an agent read the project from the conversation it works in.

        Two conditions. The agent sits in that conversation's room, the same
        question ``authorize_topic`` asks of every room route. And everyone the
        room shows its answer to may read the project themselves: what the
        agent reads ends up in the room, so a room with somebody from outside
        the project would hand that person what the project keeps from them.
        """
        if await conversations.project_of(self._session, conversation_id) != project_id:
            raise ForbiddenError(say("projectMemberOnly"))
        room_id = await conversations.room_of(self._session, conversation_id)
        await self.authorize_topic(
            actor, project_id=project_id, topic_id=room_id, enforce=True
        )
        if await outsiders_reading(
            self._session, project_id=project_id, room_id=room_id
        ):
            raise ForbiddenError(say("projectReadsWithOutsiders"))

    async def authorize_task(self, actor: Actor, *, task_id: int) -> None:
        """Require a verified caller who may see this 赛题.

        ``GET /projects/by-task/{task_id}`` is the 赛题 page asking what already
        exists for a task, and every row it answers with carries a project id, a
        name and an owner handle. The task id is a small integer, so "what
        exists for task 42" is not public information - it is the directory the
        rest of the project routes take their ids from. The judgment is
        ``TaskVisibilityService``, the same one the task page itself uses,
        rather than a second copy of it here.

        A task that does not exist is let through: the route answers an empty
        page for it either way, and a 403 there would turn this into a probe
        for which task ids are real.
        """
        if not settings.authz_enforce_topic_access:
            return
        self.reject_failed_credential(actor)
        if not actor.authenticated:
            if is_global_sandbox_token(self._cheese_token):
                return  # Trusted development credential; anonymous access stays denied.
            raise AuthenticationRequiredError(say("signInForTask"))
        task = await TaskRepository(self._session).get_by_id(task_id)
        if task is None:
            return
        user_id = actor.user_id
        if user_id is None:
            # A handle-only session token carries no int user id, and task
            # visibility is keyed by one - see ``_is_team_member`` above for the
            # same resolution.
            user = await UserRepository(self._session).get_by_username(actor.handle)
            user_id = user.id if user is not None else None
        if user_id is not None and await TaskVisibilityService(
            self._session
        ).can_view_task(task=task, user_id=user_id):
            return
        _log.info("task_access_denied", handle=actor.handle, task=task_id)
        raise ForbiddenError(say("challengeViewForbidden"))

    async def authorize_team(self, actor: Actor, *, team_id: int) -> None:
        """Require a verified member of this team.

        A team's project list is not a directory: it carries every project's
        ``id``, and the id is the key to that project's roster, documents and
        usage. So listing somebody else's team leaks whatever those routes
        expose, which is why this guard sits alongside ``authorize_project``
        rather than being folded into a milder "is anyone logged in" check.
        """
        if not settings.authz_enforce_topic_access:
            return
        self.reject_failed_credential(actor)
        if not actor.authenticated:
            if is_global_sandbox_token(self._cheese_token):
                return  # Trusted development credential; anonymous access stays denied.
            raise AuthenticationRequiredError(say("signInForTeam"))
        if await self._is_team_member(team_id, actor.handle):
            return
        _log.info("team_access_denied", handle=actor.handle, team=team_id)
        raise ForbiddenError(say("teamMemberOnly"))

    async def _is_team_member(self, team_id: int, handle: str) -> bool:
        """Team membership is keyed by user id while every other authorization
        key is the handle string, so the handle is resolved to its user here —
        see the same note in ``_is_project_member``."""
        user = await UserRepository(self._session).get_by_username(handle)
        if user is None:
            return False
        return await TeamRepository(self._session).is_team_member(team_id, user.id)

    async def _is_project_member(self, project_id: uuid.UUID, handle: str) -> bool:
        """The one notion of 项目成员 both guards share: on the project's roster,
        its owner, or a member of the team the project belongs to — unless they
        have left THIS project (``ProjectMemberExclusion``).

        Those are exactly the three claims ``ProjectRepository.list_visible_to``
        lists a project under. Until 2026-09-04 the guards accepted only the
        first two, so a teammate saw the project in their sidebar and on the
        team page, clicked in, and the topic list answered 403 — the listing
        promised what the door refused. Measured on dev: a member who had
        accepted a team invitation minutes earlier got 200 on
        ``/projects/{id}`` and 403 on ``/topics?project_id=``.

        This is the injection point for ``authorize_topic_access``'s
        ``is_project_member``: ``can_access_topic`` hands that guard this very
        method, so the room-seat path and the project path answer with one
        judgment and no second declaration of "谁是项目成员" exists.

        The claim set itself now lives in ``app.auth.project_access`` so that
        every route reading a project's conversations asks the same question —
        this method is the in-request form of it."""
        return await may_read_project(
            self._session, project_id=project_id, handle=handle
        )

    async def project_of_topic(self, topic_id: uuid.UUID) -> uuid.UUID | None:
        """The project of a conversation: a room's, or a task's."""
        return await conversations.project_of(self._session, topic_id)


def get_actor_resolver(
    request: Request, db: Annotated[AsyncSession, Depends(get_db)]
) -> ActorResolver:
    return ActorResolver(
        session=db,
        bearer=_bearer(request.headers.get("authorization")),
        cheese_token=request.headers.get("x-cheese-token") or "",
        screen_token=request.headers.get("x-cheese-screen") or "",
        writes=request.method not in ("GET", "HEAD", "OPTIONS"),
        endpoint=_endpoint_name(request),
    )


def _endpoint_name(request: Request) -> str | None:
    endpoint = request.scope.get("endpoint")
    if endpoint is None:
        return None
    return f"{endpoint.__module__}.{endpoint.__qualname__}"


# Browsers cannot set an Authorization header on a WebSocket, so the chat route
# builds the resolver from the ?token= query param itself (照 reference
# viewer_authz.py) — see app/api/routes/chat.py.
ActorResolverDep = Annotated[ActorResolver, Depends(get_actor_resolver)]


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
