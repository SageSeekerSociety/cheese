"""Resolve agent authors and explicitly declared human contribution credits."""

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.core.config import settings
from app.domain.identity.handles import looks_like_agent_handle, topic_agent_handle

if TYPE_CHECKING:
    from app.domain.topic.models import Topic

logger = logging.getLogger("cheesex.workspace.identity")

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
    """One piece of work that went into a delivery — a `tasks` row and the 分身
    that did it.

    A commit's `Co-authored-by` names people; this names MACHINES, which is a
    different question and needs a different answer. `Claude Fable 5` is on every
    commit any Claude Code writes anywhere, so it cannot tell you which worker in
    which room typed this one. `subagent_id` can: it is the id Claude Code minted
    for that worker inside the room's session, the same string the room's hook
    events carry, so a line of `git log` and a thread in the room name the same
    machine."""

    task_id: uuid.UUID
    #: NULL while nobody has claimed the work — a task row exists from the moment
    #: it is dispatched and the worker is bound a moment later, so a delivered
    #: task can honestly have none.
    subagent_id: str | None
    title: str
    reporter_handle: str | None = None
    contributor_handles: tuple[str, ...] = ()


@dataclass(frozen=True)
class Attribution:
    """Agent author and independently resolved human contribution roles."""

    #: The human requester handle.
    handle: str | None
    #: The platform agent that authored the work.
    author: GitIdentity | None
    #: Explicitly declared human code contributors.
    coauthors: tuple[GitIdentity, ...] = ()
    #: `Cheese-Task:`, one line each. See `work_items`.
    tasks: tuple[WorkItem, ...] = ()
    #: `Reviewed-by:`'s git identity — the accepter's, when they connected
    #: GitHub. Resolved here rather than at the trailer builder for the same
    #: reason as `author`: that module is pure, so anything needing a session
    #: arrives already resolved.
    reviewer: GitIdentity | None = None
    requester: GitIdentity | None = None
    reporters: tuple[GitIdentity, ...] = ()


__all__ = [
    "CHEESE_EMAIL",
    "CHEESE_IDENTITY",
    "CHEESE_NAME",
    "Attribution",
    "GitIdentity",
    "WorkItem",
    "attribution",
    "coauthored_by",
    "identity_from_profile",
    "as_trailer",
    "noreply_email",
    "platform_identity",
    "requester_handle",
    "resolve_for_handle",
    "session_dir",
    "work_items",
]


def session_dir(project_id: uuid.UUID, topic_id: uuid.UUID) -> Path:
    """Directory for this room's session logs and command spools."""
    return (
        Path(settings.workspace_root) / ".sessions" / str(project_id) / topic_id.hex[:8]
    ).resolve()


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
    from app.domain.oauth.services import get_github_profile_for_handle

    if not handle:
        return None
    found = await get_github_profile_for_handle(session, handle)
    if found is None:
        return None
    return identity_from_profile(*found)


async def requester_handle(
    session: Any, topic: "Topic", *, task_id: uuid.UUID | None = None
) -> str | None:
    """Resolve the requester from task ownership, then the room roster."""
    if task_id is not None:
        thread = await _thread(session, task_id)
        if thread is not None:
            if thread.owner_handle and not looks_like_agent_handle(thread.owner_handle):
                return thread.owner_handle
            return thread.created_by or None
    owner = await _roster_owner(session, topic.id)
    if owner and not looks_like_agent_handle(owner):
        return owner
    return topic.created_by or None


async def _thread(session: Any, task_id: uuid.UUID):
    """The thread row, or None if it cannot be read. Never raises, same rule as
    `_roster_owner`: attribution must not be why a PR fails to open."""
    try:
        from app.domain.room_task.services import TaskService

        return await TaskService(session).get(task_id)
    except Exception:  # noqa: BLE001 — attribution never fails its caller
        logger.info("could not read thread %s", task_id, exc_info=True)
        return None


async def _roster_owner(session: Any, topic_id: uuid.UUID) -> str | None:
    """The room's owner per its roster, or None if it cannot be read. Never
    raises: attribution must not be the reason a PR fails to open, so a broken
    roster read degrades to "no answer" rather than to an exception."""
    try:
        from app.domain.topic_membership.services import TopicMemberService

        return await TopicMemberService(session).owner_of(topic_id)
    except Exception:  # noqa: BLE001 — attribution never fails its caller
        logger.info("could not read roster owner for topic %s", topic_id, exc_info=True)
        return None


async def work_items(session: Any, card: Any) -> tuple[WorkItem, ...]:
    """Every piece of work this delivery carries, oldest first — read from what
    the card DECLARES (`AcceptCard.delivered_task_ids`), and from nowhere else.

    It used to be derived: the batch was taken to be the membership of the tree
    the card delivered. That is wrong whenever a room works across two batches,
    which is the ordinary case. A task's tree is fixed when `cheese split` runs
    and records which batch was open THEN; which branch its code goes out on is
    decided when the room files a card. Run that inference over this project's
    own room/task/tree data as of 2026-09-08 and one delivery comes out wrong in
    both directions at once: it would be signed by three tasks that contributed
    nothing to it, while the task that actually wrote it would be signed onto
    the previous delivery.

    No fallback, deliberately. A card that declares nothing produces no
    `Cheese-Task:` line, and falling back to the tree "just for those" would
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
    from app.domain.room_task.services import TaskService

    return tuple(
        WorkItem(
            task.id,
            task.subagent_id or None,
            task.title or "",
            getattr(task, "reporter_handle", None),
            tuple(getattr(task, "contributor_handles", None) or ()),
        )
        for task in await TaskService(session).list_by_ids(declared)
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
    session: Any, topic: "Topic", *, card: Any = None, decided_by: str | None = None
) -> "Attribution":
    """Resolve the agent author and credits from this delivery's declared tasks.

    Ownership alone never establishes a code contribution or a bug report."""
    task_id = getattr(card, "task_id", None)
    handle: str | None = None
    try:
        handle = await requester_handle(session, topic, task_id=task_id)
    except Exception:  # noqa: BLE001 — a trailer is not worth failing a merge
        logger.warning(
            "could not resolve the requester for topic %s", topic.id, exc_info=True
        )
    requester = await _identity_of(session, handle)
    author = agent_identity(topic_agent_handle(topic.id))
    tasks: tuple[WorkItem, ...] = ()
    try:
        tasks = await work_items(session, card)
    except Exception:  # noqa: BLE001 — same rule again: a trailer, not a gate
        logger.warning(
            "could not resolve the work behind topic %s", topic.id, exc_info=True
        )
    coauthors: list[GitIdentity] = []
    reporters: list[GitIdentity] = []
    for item in tasks:
        for person, credits in [
            *[(person, coauthors) for person in item.contributor_handles],
            *([(item.reporter_handle, reporters)] if item.reporter_handle else []),
        ]:
            found = await _identity_of(session, person) or platform_identity(person)
            if found not in credits:
                credits.append(found)
    return Attribution(
        handle,
        author,
        tuple(coauthors),
        tasks,
        await _identity_of(session, decided_by),
        requester,
        tuple(reporters),
    )


async def _identity_of(session: Any, handle: str | None) -> GitIdentity | None:
    """`resolve_for_handle` that answers None instead of raising — one unreadable
    OAuth connection costs that one person's link, nothing else."""
    if not handle:
        return None
    try:
        return await resolve_for_handle(session, handle)
    except Exception:  # noqa: BLE001 — a trailer is not worth failing a merge
        logger.warning("could not resolve a git identity for %s", handle, exc_info=True)
        return None


def coauthored_by(identity: GitIdentity | None) -> str | None:
    """Format a declared human code contributor as a Git trailer."""
    if identity is None or identity == CHEESE_IDENTITY:
        return None
    return f"Co-authored-by: {identity.name} <{identity.email}>"
