"""Resolve who a delivery is credited to: the task owner, the agent, and the
declared contributors, under the project's credit mode."""

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.core.config import settings
from app.domain.identity.handles import looks_like_agent_handle
from app.domain.repository import task_rows

if TYPE_CHECKING:
    from app.domain.topic.models import Topic

logger = logging.getLogger("cheesex.repository.identity")

#: The platform identity used for mechanical commit operations.
CHEESE_NAME = "芝士"
CHEESE_EMAIL = "cheese@zhishi.local"


@dataclass(frozen=True)
class GitIdentity:
    name: str
    email: str

    def __str__(self) -> str:
        return f"{self.name} <{self.email}>"


CHEESE_IDENTITY = GitIdentity(CHEESE_NAME, CHEESE_EMAIL)


@dataclass(frozen=True)
class WorkItem:
    """One piece of work that went into a delivery — a `tasks` row."""

    task_id: uuid.UUID
    title: str
    reporter_handle: str | None = None
    contributor_handles: tuple[str, ...] = ()


#: The project setting that chooses who a delivery is credited to.
CREDIT_SETTING = "commit_credit"
#: The task owner opens the PR and authors the commit; the AI teammate is a
#: co-author.
CREDIT_OWNER = "owner"
#: The AI teammate opens the PR and authors the commit; the owner is not named,
#: and the `Link:` trailer leads back to the task.
CREDIT_AGENT = "agent"
CREDIT_MODES = (CREDIT_OWNER, CREDIT_AGENT)


def credit_mode(project_settings: dict) -> str:
    """The project's credit mode; unset reads as `CREDIT_OWNER`."""
    chosen = project_settings.get(CREDIT_SETTING)
    return chosen if chosen in CREDIT_MODES else CREDIT_OWNER


@dataclass(frozen=True)
class Attribution:
    """Who a delivery is credited to, resolved once for every text it writes.

    One rule decides every commit it touches: on GitHub the task owner authors
    the work when the project credits owners and the task has one; otherwise
    the AI teammate does. Everyone else credited — the owner when they are not
    the author, the declared contributors, and the AI teammate when it is not
    — is a `Co-authored-by`."""

    #: The task owner's handle: the human the work is for, or None when no
    #: human owns the task.
    owner_handle: str | None
    #: The platform agent that wrote the work. It is never shown: the AI
    #: teammate appears as `app` on GitHub and as 芝士 on the platform forge.
    author: GitIdentity | None
    #: `CREDIT_OWNER` or `CREDIT_AGENT`.
    mode: str = CREDIT_OWNER
    #: The owner's git identity — their GitHub one when signed in with it.
    owner: GitIdentity | None = None
    #: People the task declares as having written part of it.
    contributors: tuple[GitIdentity, ...] = ()
    #: `Link:`, one line each. See `work_items`.
    tasks: tuple[WorkItem, ...] = ()
    #: `Reviewed-by:`'s git identity — the accepter's, when they connected
    #: GitHub. Resolved here rather than at the trailer builder for the same
    #: reason as `author`: that module is pure, so anything needing a session
    #: arrives already resolved.
    reviewer: GitIdentity | None = None
    reporters: tuple[GitIdentity, ...] = ()
    #: The address of the human whose GitHub account opened the PR, None when
    #: the App did (or there is no PR). GitHub makes the PR's opener the author
    #: of its squash commit, so this is who the landed commit is by.
    opened_by: str | None = None
    #: The GitHub App's own identity — the AI teammate on GitHub, shown with
    #: the App's logo. None off GitHub, or when it could not be read.
    app: GitIdentity | None = None
    #: Where the project's code lives: `"github"`, `"forgejo"`, or None when
    #: there is no repository.
    lane: str | None = None

    @property
    def ai(self) -> GitIdentity | None:
        """How the AI teammate is named in git on this project's forge.

        On the platform forge it keeps its own address — each address there
        belongs to exactly one account, and the project's account claims the
        agent's (`project.forge.ensure_author_email`) — under the name 芝士
        rather than an internal handle nobody can read."""
        if self.lane == "github":
            return self.app
        if self.lane == "forgejo" and self.author is not None:
            return GitIdentity(CHEESE_NAME, self.author.email)
        return None

    @property
    def commit_author(self) -> GitIdentity | None:
        """The author of the commits the agent makes on the task branch.

        The owner only on GitHub: on the platform forge an author's address
        has to be claimed by the project's account, and one person's address
        cannot belong to every project they own a task in."""
        if self.lane == "github" and self.mode == CREDIT_OWNER and self.owner:
            return self.owner
        return self.ai or self.author

    @property
    def commit_coauthors(self) -> tuple[GitIdentity, ...]:
        """The co-authors of those commits."""
        author = self.commit_author
        return self._credited_besides(author.email if author else None)

    @property
    def coauthors(self) -> tuple[GitIdentity, ...]:
        """The co-authors of the commit that lands. On GitHub its author is
        whoever opened the PR: a person (`opened_by`) or the App. The platform
        forge keeps the branch's own author."""
        if self.lane == "github":
            landed = self.opened_by or (self.app.email if self.app else None)
        else:
            author = self.commit_author
            landed = author.email if author else None
        return self._credited_besides(landed)

    def _credited_besides(self, author_email: str | None) -> tuple[GitIdentity, ...]:
        credited: list[GitIdentity] = []
        if self.mode == CREDIT_OWNER and self.owner is not None:
            credited.append(self.owner)
        credited.extend(self.contributors)
        if self.ai is not None:
            credited.append(self.ai)
        unique: list[GitIdentity] = []
        for person in credited:
            if person.email != author_email and person not in unique:
                unique.append(person)
        return tuple(unique)


__all__ = [
    "CHEESE_EMAIL",
    "CHEESE_IDENTITY",
    "CHEESE_NAME",
    "Attribution",
    "GitIdentity",
    "WorkItem",
    "CREDIT_AGENT",
    "CREDIT_MODES",
    "CREDIT_OWNER",
    "CREDIT_SETTING",
    "attribution",
    "app_identity",
    "coauthored_by",
    "credit_mode",
    "identity_from_profile",
    "as_trailer",
    "noreply_email",
    "platform_identity",
    "owner_handle",
    "resolve_for_handle",
    "work_items",
]


def platform_identity(handle: str) -> GitIdentity:
    """The address for somebody who never connected GitHub (#189).

    A trailer has to name a person, and a bare handle names a string. This is
    the honest degrade: the platform's own domain, the same one 芝士 commits
    under, so it is a well-formed address that git and GitHub both accept and
    that no reader can mistake for a real mailbox or for a GitHub account. What
    it must NEVER be is a fabricated `users.noreply.github.com` address — that
    one LOOKS linkable and points at nobody, which is worse than admitting we
    have no account for them.
    """
    return GitIdentity(handle, f"{handle}@{CHEESE_EMAIL.split('@', 1)[1]}")


def agent_identity(handle: str) -> GitIdentity:
    """The acting platform agent, without inventing a GitHub account."""
    return GitIdentity(handle, f"{handle}@agent.cheese.local")


def as_trailer(handle: str | None, resolved: "GitIdentity | None") -> str:
    """`Name <email>` for a trailer — resolved identity first, platform address
    otherwise, and the bare handle only when there is no handle to build from."""
    if resolved is not None:
        return f"{resolved.name} <{resolved.email}>"
    if not handle:
        return ""
    who = platform_identity(handle)
    return f"{who.name} <{who.email}>"


def noreply_email(github_user_id: str, login: str) -> str:
    """GitHub's per-account no-reply address. Always linkable, never leaks a
    private address."""
    return f"{github_user_id}+{login}@users.noreply.github.com"


def identity_from_profile(
    github_user_id: str | None, raw_profile: dict[str, Any] | None
) -> GitIdentity | None:
    """A git identity from a stored OAuth connection, or None when the
    connection cannot produce a linkable one. The numeric id must be numeric:
    GitHub matches the address on that number, and a non-numeric one would
    produce a plausible-looking address that links to nobody — worse than
    admitting we have no identity."""
    login = str((raw_profile or {}).get("login") or "").strip()
    if not github_user_id or not login or not str(github_user_id).isdigit():
        return None
    name = str((raw_profile or {}).get("name") or "").strip() or login
    return GitIdentity(name, noreply_email(str(github_user_id), login))


async def resolve_for_handle(session: Any, handle: str) -> GitIdentity | None:
    """The linked Git identity of a platform user, or None without a connection.

    The connection lookup belongs to the oauth domain and is asked for as a
    service call, not by reaching into its repositories (the ratchet in
    tests/unit/test_domain_import_guard.py). What stays here is the only part
    that is about git: turning an account into an address."""
    # deferred-import: tests replace this name on app.domain.oauth.services
    from app.domain.oauth.services import get_github_profile_for_handle

    if not handle:
        return None
    found = await get_github_profile_for_handle(session, handle)
    if found is None:
        return None
    return identity_from_profile(*found)


async def owner_handle(session: Any, task_id: uuid.UUID | None) -> str | None:
    """The human the task is for: its owner, else whoever created it.

    None when neither is a human. A task an agent opened and owns has nobody
    behind it, and crediting the channel's owner instead would put the name of
    someone who may never have seen the change on it."""
    if task_id is None:
        return None
    thread = await _thread(session, task_id)
    if thread is None:
        return None
    for handle in (thread.owner_handle, thread.created_by):
        if handle and not looks_like_agent_handle(handle):
            return handle
    return None


async def _thread(session: Any, task_id: uuid.UUID):
    """The task's row, or None if it cannot be read. Never raises: attribution
    must not be why a PR fails to open.

    A plain column read (`repository.task_rows`): `room_task` is a layer above
    this package, and this one needs the row, not that domain's behaviour."""
    try:
        return await task_rows.one(session, task_id)
    except Exception:  # attribution never fails its caller
        logger.info("could not read thread %s", task_id, exc_info=True)
        return None


async def work_items(session: Any, card: Any) -> tuple[WorkItem, ...]:
    """Every piece of work this delivery carries, oldest first — read from what
    the card DECLARES (`AcceptCard.delivered_task_ids`), and from nowhere else.

    It used to be derived: the batch was taken to be the membership of the tree
    the card delivered. That is wrong whenever a room works across two batches,
    which is the ordinary case. A task's tree is fixed when the task is created
    and records which batch was open THEN; which branch its code goes out on is
    decided when the room files a card. Run that inference over this project's
    own room/task/tree data as of 2026-09-08 and one delivery comes out wrong in
    both directions at once: it would be signed by three tasks that contributed
    nothing to it, while the task that actually wrote it would be signed onto
    the previous delivery.

    No fallback, deliberately. A card that declares nothing produces no
    `Link:` line to a task, and falling back to the tree "just for those" would
    quietly restore exactly the wrong answers this replaced. An audit believes a
    trailer; a wrong name is worse than a missing one.

    Empty is an ordinary answer and never an error: a room that dispatched no
    work has none, a card filed before this existed has none, and a batch that
    cannot be read degrades to "no such trailers" rather than taking the merge
    down with it.
    """
    declared = [
        parsed
        for parsed in (_as_uuid(raw) for raw in getattr(card, "delivered_task_ids", []))
        if parsed is not None
    ]
    if not declared:
        return ()
    return tuple(
        WorkItem(
            task.id,
            task.title or "",
            getattr(task, "reporter_handle", None),
            tuple(getattr(task, "contributor_handles", None) or ()),
        )
        for task in await task_rows.many(session, declared)
    )


def _as_uuid(raw: Any) -> uuid.UUID | None:
    """A declared id, or None when the column holds something that is not one.
    JSON has no uuid type, so what comes back is whatever was written."""
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        return None


async def attribution(
    session: Any,
    topic: "Topic",
    *,
    card: Any = None,
    decided_by: str | None = None,
    task_id: uuid.UUID | None = None,
    opened_by: str | None = None,
    app: GitIdentity | None = None,
    lane: str | None = None,
) -> "Attribution":
    """Resolve who this delivery is credited to.

    `lane`, `opened_by` and `app` describe the forge and the PR, which only
    the caller holding them knows: where the code lives, the address of the
    human whose account opened the PR, and the GitHub App's identity.
    Contributors and reporters come from task declarations."""
    # deferred-import: tests replace this name on app.domain.topic_membership.services
    from app.domain.topic_membership.services import TopicMemberService

    task_id = task_id or getattr(card, "task_id", None)
    handle: str | None = None
    try:
        handle = await owner_handle(session, task_id)
    except Exception:  # a trailer is not worth failing a merge
        logger.warning(
            "could not resolve the owner for topic %s", topic.id, exc_info=True
        )
    owner = (
        (await _identity_of(session, handle) or platform_identity(handle))
        if handle
        else None
    )
    if task_id:
        task = await _thread(session, task_id)
        acting = getattr(task, "author_handle", None)
    else:
        acting = await TopicMemberService(session).resolve_agent_handle(topic.id)
    author = agent_identity(acting) if acting else None
    tasks: tuple[WorkItem, ...] = ()
    try:
        tasks = await work_items(session, card)
        if task_id and not any(item.task_id == task_id for item in tasks):
            task = await _thread(session, task_id)
            if task is not None:
                tasks = (
                    *tasks,
                    WorkItem(
                        task.id,
                        task.title,
                        task.reporter_handle,
                        tuple(task.contributor_handles or ()),
                    ),
                )
    except Exception:  # same rule again: a trailer, not a gate
        logger.warning(
            "could not resolve the work behind topic %s", topic.id, exc_info=True
        )
    # deferred-import: tests replace this name on app.domain.project.services
    from app.domain.project.services import ProjectService

    project = await ProjectService(session).get(topic.project_id)
    mode = credit_mode((project.settings or {}) if project else {})
    contributors: list[GitIdentity] = []
    reporters: list[GitIdentity] = []
    for item in tasks:
        for person, credits in [
            *[(person, contributors) for person in item.contributor_handles],
            *([(item.reporter_handle, reporters)] if item.reporter_handle else []),
        ]:
            found = await _identity_of(session, person) or platform_identity(person)
            if found not in credits:
                credits.append(found)
    return Attribution(
        handle,
        author,
        mode=mode,
        owner=owner,
        contributors=tuple(contributors),
        tasks=tasks,
        reviewer=await _identity_of(session, decided_by),
        reporters=tuple(reporters),
        opened_by=opened_by,
        app=app,
        lane=lane,
    )


def app_identity(user_id: int | str) -> GitIdentity:
    """The GitHub App's bot account as a git identity. GitHub shows the App's
    logo for an address built from the bot account's numeric id."""
    login = f"{settings.github_app_slug}[bot]"
    return GitIdentity(login, noreply_email(str(user_id), login))


async def _identity_of(session: Any, handle: str | None) -> GitIdentity | None:
    """`resolve_for_handle` that answers None instead of raising — one unreadable
    OAuth connection costs that one person's link, nothing else."""
    if not handle:
        return None
    try:
        return await resolve_for_handle(session, handle)
    except Exception:  # a trailer is not worth failing a merge
        logger.warning("could not resolve a git identity for %s", handle, exc_info=True)
        return None


def coauthored_by(identity: GitIdentity | None) -> str | None:
    """Format a co-author as a Git trailer."""
    if identity is None:
        return None
    return f"Co-authored-by: {identity.name} <{identity.email}>"
