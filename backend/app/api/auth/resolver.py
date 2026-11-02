"""``ActorResolver`` — the trust boundary, out of the ``app/api/auth`` facade.

Resolve the actor (a person's bearer token, an agent's scoped credential, the
global sandbox token) and authorize the rooms, projects, tasks and teams a
request names. This is where the pure seams
(``app.domain.identity.actor``, ``app.domain.authz.policy``) meet the real
request — headers, DB, WebSocket. Routes call ``resolve(...)`` (replacing every
``body.get("author")`` trust-read) and, on authenticated writes,
``authorize_topic(...)``.

The wiring routes import — ``ActorResolverDep``, ``MediaActorResolverDep``,
the media cookie, ``require_seated_agent`` — is in the kernel of the same
package, ``app/api/auth/__init__.py``. Import that.

**Read through ``pkg``, never imported here.** ``pkg`` is the facade, and the
names below come off it at CALL time on purpose: the two repositories the tests
replace to watch this class (``UserRepository`` in
``tests/unit/test_actor_numeric_handle.py`` and
``tests/unit/test_token_scope_violation.py``, plus ``TopicMemberService`` in
those same two), the token verifier, and
``require_seated_in_its_room``. A module-local import would step past every one
of those replacements in silence; the facade is the only place a replacement is
seen (``app/domain/review/services`` reads its own patch points the same way).
"""

import copy
import uuid
from collections.abc import Sequence
from dataclasses import replace

from sqlalchemy.ext.asyncio import AsyncSession

from app.api import auth as pkg
from app.auth.audience import rooms_its_audience_may_read
from app.auth.audience import seats_only as _private
from app.auth.caller import handle_named_by
from app.auth.project_access import (
    may_read_project,
    may_write_project,
    outsiders_reading,
)
from app.auth.project_roles import may
from app.common.auth import verify_access_token
from app.core.config import settings
from app.core.errors import (
    AuthenticationRequiredError,
    ForbiddenError,
    NotFoundError,
)
from app.core.obs import get_logger
from app.core.sandbox_auth import (
    DelegatedClaims,
    delegated_claims,
    held_by_a_machine,
    is_global_sandbox_token,
    looks_like_delegated_credential,
    looks_like_project_agent_credential,
    project_agent_claims,
    scoped_token_claims,
    token_agent_handle,
    verify_scoped_token,
)
from app.core.sentences import say
from app.domain.agent_credential.services import ProjectAgentCredentialService
from app.domain.authz.policy import authorize_topic_access
from app.domain.conversation import services as conversations
from app.domain.identity.actor import Actor, resolve_actor
from app.domain.identity.handles import UNRESOLVED_AGENT_HANDLE
from app.domain.project.repositories import ProjectRepository
from app.domain.project.services import refuse_writes_if_archived
from app.domain.project_permissions import Permission
from app.domain.task.repositories import TaskRepository
from app.domain.task.visibility_service import TaskVisibilityService
from app.domain.team.repositories import TeamRepository
from app.domain.topic.models import Topic, TopicRole
from app.domain.topic.repositories import TopicRepository
from app.domain.topic_membership.repositories import TopicMembershipRepository

_log = get_logger("cheesex.auth")


class ActorResolver:
    """Per-request resolver. Reads the human bearer token + agent scoped token,
    then resolves/authorizes against the DB."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        bearer: str | None,
        cheese_token: str,
        writes: bool = False,
        allow_machine: bool = False,
        bind_user_id: bool = True,
    ):
        self._session = session
        self._allow_machine = allow_machine
        # Whether `resolve` looks up the user id of an actor whose credential
        # carried none (`_bound`). A caller that only asks where the actor sits,
        # which is answered by handle, skips that read.
        self._bind_user_id = bind_user_id
        # Whether this request changes something. A write that names a project,
        # or a room of one, is refused while that project is archived — here,
        # once, so that no write route can forget to ask (see ``resolve``).
        self._writes = writes
        self._project_credential = False
        # Set when a delegated answer is shown to a document's readers: the
        # document's task, or None for a document of no task.
        self._answer_for_readers: tuple[uuid.UUID | None] | None = None
        self._bearer = bearer
        self._cheese_token = cheese_token
        self._credentials = ProjectAgentCredentialService(session)

    def for_reading(self) -> "ActorResolver":
        """The same caller, judged as a read: for a request that changes
        something of its own (asking 芝士) but only reads where it looks (the
        page it was asked from, which an archived project still shows)."""
        reader = copy.copy(self)
        reader._writes = False
        return reader

    def credential_conversation(self) -> uuid.UUID | None:
        """The conversation the presented credential was minted in: a room, or
        a task its session works — where what the agent does elsewhere in the
        project is told. None for a person, a project credential, a delegated
        one, or a token naming no conversation."""
        claims = scoped_token_claims(self._cheese_token) if self._cheese_token else None
        named = (claims or {}).get("t")
        return uuid.UUID(named) if named else None

    def credential_claims(self) -> dict | None:
        """The claims of the presented session credential (a scoped token);
        None for anything else."""
        return scoped_token_claims(self._cheese_token) if self._cheese_token else None

    def credential_project(self) -> uuid.UUID | None:
        """The project the presented session credential was minted in; None for
        a person or a token naming no project."""
        claims = scoped_token_claims(self._cheese_token) if self._cheese_token else None
        named = (claims or {}).get("p")
        return uuid.UUID(named) if named else None

    async def _confine_task_credential(self, topic_id: uuid.UUID | None) -> None:
        """A credential minted for a task's conversation acts in no other
        conversation — not its room, not another task — even the session-wide
        one that otherwise reaches across its project. Routes that name no
        conversation (the project's library, its documents) it reaches as any
        session of the project does.

        The refusal points the way out rather than naming the rule: a session
        that stopped here once stood still for a task next to its own, while
        what the task said stays readable through the channel-scoped search.
        """
        if not self._cheese_token or topic_id is None:
            return
        claims = scoped_token_claims(self._cheese_token)
        if claims is None or (not self._allow_machine and held_by_a_machine(claims)):
            return
        named = claims.get("t")
        if not named or named == str(topic_id):
            return
        if await conversations.is_task(self._session, uuid.UUID(named)):
            raise ForbiddenError(say("taskCredentialOtherConversation"))

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
        when the credential is not valid, the request changes something and the
        question may only be answered, or the route acts somewhere the
        credential was not minted for.

        Which of the person's actions 芝士 takes, and which it asks about
        first, is its harness's to decide before a tool runs
        (``run_platform_tool``), not this credential's: it never reaches a
        place the model could use it from."""
        claims = self.delegation()
        if claims is None:
            raise AuthenticationRequiredError(
                "Delegated credential is invalid or expired"
            )
        if self._writes and not read_only and claims.read_only:
            raise ForbiddenError("This question may only be answered, not act")
        if topic_id is not None and project_id is None:
            project_id = await self.project_of_topic(topic_id)
        if claims.agent is None and claims.topic_id is None:
            # A person's own 芝士 (no agent, no room) reads and acts wherever the
            # person may, as them: the route judges the person, the credential
            # holds it to no project. The project it was minted in is only
            # where the question was asked.
            pass
        elif claims.project_id is None:
            if topic_id is not None or project_id is not None:
                raise ForbiddenError(say("tokenOtherProject"))
        elif project_id is None and topic_id is None:
            # One minted for a room has nothing here to hold it to.
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
        if claims.shown_to_readers:
            self._answer_for_readers = (
                uuid.UUID(claims.topic_id) if claims.topic_id else None,
            )
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
            self._project_credential = True
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
                allow_machine=self._allow_machine,
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
            verify_token=pkg._token_verifier,
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
                and (claims := scoped_token_claims(self._cheese_token)) is not None
                and (self._allow_machine or not held_by_a_machine(claims))
            ):
                raise ForbiddenError(
                    "This credential is restricted to one project; "
                    "call a route that names the project or room"
                )
            raise AuthenticationRequiredError("Agent credential is invalid or expired")
        actor = await self._bound(actor)
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
        return await pkg.TopicMemberService(self._session).resolve_agent_handle(
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
        regex does not cover) can silently drop, so the route enforces it itself.
        The global ``SANDBOX_TOKEN`` stays gate-only (dev / trusted-single-host
        override): it opens the surface but never becomes an identity — same
        rule as ``resolve()``.
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
        await pkg.require_seated_in_its_room(
            self._session, self._cheese_token, project_id=project_id
        )

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
        if claims is None or (not self._allow_machine and held_by_a_machine(claims)):
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

    async def _bound(self, actor: Actor) -> Actor:
        """``actor`` named by both its handle and its user id.

        An authenticated actor whose credential carried no int PK: look the
        row up by handle, because int-keyed rows (device.owner_user_id) cannot
        be bound without it. Asked of every such actor rather than only of the
        agents — the caller's KIND was never what made the id missing (a
        legacy cheesex token puts the handle in ``sub`` and carries no id
        either), so branching on it just left those callers unbound."""
        actor = await self._recover_numeric_handle(actor)
        if self._bind_user_id and actor.authenticated and actor.user_id is None:
            user = await pkg.UserRepository(self._session).get_by_username(actor.handle)
            if user is not None:
                actor = replace(actor, user_id=user.id)
        return actor

    def signed_in_handle(self) -> str | None:
        """The handle a person's verified sign-in names, read off the token
        without the database; None for any other caller. A hint for reading
        ahead (`place_or_404(member=...)`), never a decision: `resolve` still
        decides who the caller is."""
        if self._cheese_token or not self._bearer:
            return None
        claims = verify_access_token(self._bearer)
        # A token that states no handle carries the user id in its place; the
        # handle is then the account's, which only the database knows.
        if claims is None or not claims.handle_from_claim:
            return None
        return claims.handle

    def presented_sign_in(self) -> bool:
        """Whether the request carried a person's sign-in at all — verified or
        not. A route that answers per caller turns a failed one into a 401
        rather than an anonymous answer (`reject_failed_credential`)."""
        return bool(self._bearer)

    async def person(self) -> Actor | None:
        """The person this request acts as, for the routes that know only
        people (a team, a space, an account): their own sign-in, or their 芝士
        on a route listed for it (`resolve` judges that, and refuses the rest).
        An AI teammate's credential and the development override name no
        person here; neither does a sign-in that does not verify."""
        if looks_like_delegated_credential(self._cheese_token):
            actor = await self.resolve()
        elif self._bearer:
            identity = pkg._token_verifier(self._bearer)
            if identity is None:
                return None
            actor = await self._bound(
                Actor(handle=identity.handle, user_id=identity.user_id, via="token")
            )
        else:
            return None
        return actor if actor.user_id is not None else None

    async def _recover_numeric_handle(self, actor: Actor) -> Actor:
        """A token actor named the way every reader names one
        (`app.auth.caller.handle_named_by`): by the handle its token states, or,
        for a token that states none, by the user its id points at. Every
        authorization key in the platform is the handle STRING, so a token
        named by its int PK would match no roster."""
        if actor.via != "token" or not self._bearer:
            return actor
        claims = verify_access_token(self._bearer)
        if claims is None:
            return actor
        return replace(actor, handle=await handle_named_by(self._session, claims))

    async def authorize_topic(
        self,
        actor: Actor,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID | None = None,
        room: Topic | None = None,
        enforce: bool = False,
    ) -> None:
        """Require a verified participant with access to this conversation's
        room.

        Giving the room row (``Place.room``) is for a caller that already holds
        it: the room the roster is asked about is that row's, and neither the
        conversation→room lookup nor the row read behind ``can_access_topic`` is
        made again. ``topic_id`` alone still works — it may be a task's or a
        支线's, and is resolved to its room there.
        """
        if room is None and topic_id is None:
            raise ValueError(  # i18n-exempt: programmer invariant, never shown
                "authorize_topic needs topic_id or room"
            )
        await self._refuse_archived_write(
            project_id=project_id,
            topic_id=room.id if room is not None else topic_id,
        )
        if not enforce and not settings.authz_enforce_topic_access:
            return
        self.reject_failed_credential(actor)
        if not actor.authenticated:
            if is_global_sandbox_token(self._cheese_token):
                return  # Trusted development credential; anonymous access stays denied.
            raise AuthenticationRequiredError(say("signInForRoom"))
        if not await self.can_access_topic(
            actor, project_id=project_id, topic_id=topic_id, room=room
        ):
            if room is not None:
                room_id = room.id
            else:
                assert topic_id is not None  # refused above when both are absent
                room_id = await conversations.room_of(self._session, topic_id)
            _log.info("topic_access_denied", handle=actor.handle, topic=str(room_id))
            row = (
                room
                if room is not None
                else await TopicRepository(self._session).get(room_id)
            )
            if row is not None and row.members_only:
                # Someone outside a private channel is not told it exists: the
                # answer is the one a channel that was never made gets.
                raise NotFoundError(say("topicNotFound"))
            raise ForbiddenError(say("topicMemberOnly"))

    async def can_access_topic(
        self,
        actor: Actor,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID | None = None,
        room: Topic | None = None,
    ) -> bool:
        """Check actual membership even on isolated content hosts in dev mode.

        ``room`` is the room's row when the caller already holds it: the room
        asked about is that row's, so neither the conversation→room lookup nor
        the row read is repeated. Without it, ``topic_id`` is resolved to its
        room and that row is read.
        """
        if room is None:
            if topic_id is None:
                raise ValueError(  # i18n-exempt: programmer invariant, never shown
                    "can_access_topic needs topic_id or room"
                )
            room = await TopicRepository(self._session).room_of_conversation(
                topic_id, member=actor.handle if actor.authenticated else None
            )
            topic_id = (
                room.id
                if room is not None
                else await conversations.room_of(self._session, topic_id)
            )
        else:
            # The room's id, not the conversation's: a task's or a 支线's id
            # names no room and no roster entry.
            topic_id = room.id
        rooms = [(topic_id, _private(room))]
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

        readable = {
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
        if self._writes or not readable:
            return readable
        # An agent's reads end up in its answer: only what everyone the answer
        # reaches may read themselves (``app.auth.audience``).
        conversation = self.turn_conversation(actor)
        if conversation is not None:
            answer_room = await conversations.room_of(self._session, conversation)
        elif self._project_credential and actor.via == "cheese":
            answer_room = None
        elif self._answer_for_readers is not None and actor.via == "delegated":
            (task,) = self._answer_for_readers
            answer_room = (
                await conversations.room_of(self._session, task) if task else None
            )
        else:
            return readable
        return readable & await rooms_its_audience_may_read(
            self._session,
            project_id=project_id,
            answer_room=answer_room,
            room_ids=list(readable),
        )

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
        door's own method answers, which keeps the access rules' one question of
        whether a room is a private chat where it is (``seats_only``;
        ``tests/unit/test_is_private_read_points.py`` allows no Python read of the
        flag outside ``topic/models.py``: a second read point is a second declaration
        of the same fact, and two declarations drift). What is taken as given is
        the credential half only — a handle in a body presents nothing, and this
        asks whether the room would admit them if it did.
        """
        if not actor.authenticated:
            return False
        return await self.can_access_topic(
            replace(actor, handle=handle), project_id=project_id, topic_id=topic_id
        )

    async def authorize_project(
        self,
        actor: Actor,
        *,
        project_id: uuid.UUID,
        permission: Permission | None = None,
    ) -> None:
        """Require a verified participant with project membership.

        An agent's per-turn credential reads the project from the conversation
        it was minted in instead (``authorize_reading_from``); what it writes
        still needs the membership it does not have.

        ``permission`` is what a write needs (``project_roles.may``); without one,
        a write needs someone who works in the project rather than looking on.

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
            if self._writes and not await (
                may(self._session, project_id, actor.handle, permission)
                if permission is not None
                else may_write_project(
                    self._session, project_id=project_id, handle=actor.handle
                )
            ):
                # A write is its own people's, each by what their role lets them
                # do (#3830): a mentor looks on, and a role may have lost the
                # permission a route asks for.
                raise ForbiddenError(say("projectPermissionDenied"))
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
        if actor.via != "cheese":
            return None
        claims = scoped_token_claims(self._cheese_token)
        if not claims or not claims.get("a") or not claims.get("t"):
            return None
        try:
            return uuid.UUID(claims["t"])
        except ValueError:
            return None

    async def authorize_reading_from(
        self,
        actor: Actor,
        *,
        project_id: uuid.UUID,
        conversation_id: uuid.UUID,
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
            user = await pkg.UserRepository(self._session).get_by_username(actor.handle)
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
        user = await pkg.UserRepository(self._session).get_by_username(handle)
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
