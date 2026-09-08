"""Who a topic's commits are authored by.

Every commit the platform makes used to be authored by `芝士
<cheese@zhishi.local>` — an address that belongs to no GitHub account, so on
GitHub the work showed up as a grey unlinked name: no avatar, no link, no
contribution credit for the person who asked for it and approved it. This module
resolves the human behind a topic to a git identity GitHub *can* link, and
persists it next to the workspace so the synchronous launch path (which has no
DB session) can read it.

The address is GitHub's `<id>+<login>@users.noreply.github.com` form. It is the
only email guaranteed to resolve to the account: a user's real email may be
private, unverified, or simply different from the one they signed up with, and
any of those makes the commit unlinked again. The numeric id is what GitHub
matches on, which is why the login alone is not enough.

Who this names: the human the topic BELONGS TO — its roster owner, see
`requester_handle`. They are the one accountable for the change; the agent typed
it. That the agent typed it is not hidden, and not left to be inferred either:
the delivery commit carries `Cheese-Agent:` (which 分身) and one `Cheese-Task:`
per piece of work the card declares it delivers (which worker inside it),
resolved by `work_items`. The platform's own commits (repo init, upstream
merges) keep the 芝士 identity because nobody asked for those.

Accountable is not the same as sole contributor. A room can change hands — one
person opens it, it stalls, someone else picks it up and the sub-topics split out
of THEIR turns belong to them (`TopicService.dispatch_task`). The person who
asked in the first place still did something, so they come back as
`Co-authored-by:`; see `coauthor_handles`.

Author and committer are two knobs, and they carry different facts: the author is
the human this work belongs to (`GIT_AUTHOR_*`), the committer is 芝士, which is
who actually ran `git commit` (`GIT_COMMITTER_*`).
"""

import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.core.config import settings
from app.domain.identity.handles import looks_like_agent_handle

if TYPE_CHECKING:
    from app.domain.topic.models import Topic

logger = logging.getLogger("cheesex.workspace.identity")

#: The platform's own identity — the fallback whenever the human behind a topic
#: has no linked GitHub account, and the committer on every commit regardless.
CHEESE_NAME = "芝士"
CHEESE_EMAIL = "cheese@zhishi.local"

_IDENTITY_FILE = "git-identity.json"


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


@dataclass(frozen=True)
class Attribution:
    """Who a change belongs to, as the PR body and the squash commit say it.

    One object rather than four loose values because they are only correct
    together: `coauthors` means "credited, and not `author`", so a caller that
    took them from different resolutions could name the same person twice or lose
    a credit. Every field may be empty — nobody on the roster, nobody with a
    GitHub account to link to, no work rows behind the delivery — and that is a
    normal, silent degrade, never a reason to fail a merge."""

    #: `Requested-by:`, the git author of the branch's commits, and the account
    #: the PR is opened under. See `requester_handle`.
    handle: str | None
    #: `handle`'s git identity, or None when they never connected GitHub.
    author: GitIdentity | None
    #: `Co-authored-by:`, one line each. See `coauthor_handles`.
    coauthors: tuple[GitIdentity, ...] = ()
    #: `Cheese-Task:`, one line each. See `work_items`.
    tasks: tuple[WorkItem, ...] = ()


__all__ = [
    "CHEESE_EMAIL",
    "CHEESE_IDENTITY",
    "CHEESE_NAME",
    "Attribution",
    "GitIdentity",
    "WorkItem",
    "attribution",
    "coauthor_handles",
    "coauthored_by",
    "identity_from_profile",
    "identity_path",
    "noreply_email",
    "read",
    "remember",
    "requester_handle",
    "resolve_for_handle",
    "session_dir",
    "sync_for_topic",
    "work_items",
]


def session_dir(project_id: uuid.UUID, topic_id: uuid.UUID) -> Path:
    """Host directory holding this topic's per-session sidecars (the await log
    spool, the command spool, and the git identity below). One definition so the
    three never drift apart."""
    return (
        Path(settings.workspace_root) / ".sessions" / str(project_id) / topic_id.hex[:8]
    ).resolve()


def identity_path(project_id: uuid.UUID, topic_id: uuid.UUID) -> Path:
    return session_dir(project_id, topic_id) / _IDENTITY_FILE


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
    """The git identity of a platform user, by handle. None when they exist but
    never connected GitHub — the caller then keeps the 芝士 identity rather than
    inventing an address.

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
    """The handle of the human a topic's work belongs to — the one name behind
    `Requested-by:`, the git author of its commits, and the account the PR is
    opened under.

    NOT ``topic.created_by``. A 分身 splits its sub-topics under its OWN handle
    (``cheese-<hex12>``, see ``identity.handles``), so on every split topic
    ``created_by`` names a robot that has no GitHub account, and every one of
    those attributions silently degraded: the PR opened as ``cheesex-app[bot]``,
    its body said ``Requested-by: cheese-a7a0268b``, and the commits carried no
    ``Co-authored-by`` at all (PR #500, #504). The roster already knows better —
    ``TopicService.dispatch_task`` walks a ladder (the splitter if human, else
    the human whose turn the split came out of, else the parent room's owner, else
    the project's) precisely to seed a real human as the child's owner. This reads
    that answer instead of re-deriving it.

    For a THREAD (``task_id``) the answer is `tasks.owner_handle` — a thread has
    no roster at all, so reading one gets the ROOM's owner, and every piece of
    work in a room would be attributed to whoever opened the room. That is the
    same silent degradation as the robot handle, one level over.

    Falls back to ``created_by``, which is what every caller used before: a room
    a human opened directly is unaffected (owner and creator are the same
    person), and a room where no human can be found behaves exactly as it does
    today rather than worse. Best-effort by construction — attribution must
    never be the reason a PR fails to open, so a broken roster read is logged
    and swallowed.
    """
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


async def coauthor_handles(
    session: Any,
    topic: "Topic",
    *,
    besides: str | None,
    task_id: uuid.UUID | None = None,
) -> list[str]:
    """Humans who should be credited on this change but are not the one it is
    attributed to (`besides`, normally `requester_handle`'s answer).

    Exactly one candidate today: the ROOM's owner. When a room changes
    hands, the work dispatched out of the new driver's turns belongs to the new
    driver — that is what makes their accept card land on someone who is still
    working on it — but the person who asked for the thing in the first place did
    not stop having asked, and `Co-authored-by:` is where git records that.

    Deliberately NOT "everyone who spoke in the parent room": a trailer is a claim
    that someone contributed to this change, and handing it to every passer-by
    inflates the credit until it means nothing.

    Returns empty for a top-level room, which is the ordinary case and the reason
    `Co-authored-by:` stopped being written on most changes: one room has one git
    identity, so a self-referential trailer naming the commit's own author added
    nothing but noise."""
    # A thread's "one level up" is its room; a room's is the project root, which
    # has no owner to credit — hence the empty answer for rooms, unchanged.
    up = topic.id if task_id is not None else topic.parent_id
    if up is None:
        return []
    owner = await _roster_owner(session, up)
    if not owner or looks_like_agent_handle(owner) or owner == besides:
        return []
    return [owner]


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
        WorkItem(task.id, task.subagent_id or None, task.title or "")
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
    session: Any, topic: "Topic", *, card: Any = None
) -> "Attribution":
    """Everything a PR body and a squash commit need to say about who a change
    belongs to, resolved in ONE place.

    Takes the CARD rather than a thread id: the card is what a delivery IS, and
    it carries both halves of the answer — the thread it was filed for (whose
    owner the change belongs to) and the work it declares it delivers (which 分身
    wrote it). Passing them separately is how a caller ends up resolving the
    humans from one card and the machines from another.

    The human answers are correlated — a co-author is defined as "credited but not
    the author" — so they are resolved together rather than at each call site;
    that is how the PR-opening path and the three merge paths are kept from
    disagreeing about the same change. `tasks` rides along for the same reason:
    `pr_text` is pure, so anything needing a session has to arrive already
    resolved, and one object means the humans and the machines behind a change
    cannot come from two different reads of it.

    Best-effort, and each part fails on its own: attribution must never take a
    merge down, but one broken lookup must not cost more than it has to either —
    losing `Requested-by:` because a co-author's account could not be read would
    make the credit the trailer exists for the thing that destroys it."""
    task_id = getattr(card, "task_id", None)
    handle: str | None = None
    try:
        handle = await requester_handle(session, topic, task_id=task_id)
    except Exception:  # noqa: BLE001 — a trailer is not worth failing a merge
        logger.warning(
            "could not resolve the requester for topic %s", topic.id, exc_info=True
        )
    author = await _identity_of(session, handle)
    coauthors: list[GitIdentity] = []
    try:
        for who in await coauthor_handles(
            session, topic, besides=handle, task_id=task_id
        ):
            found = await _identity_of(session, who)
            # `!= author` again on the resolved identity, not just on the handle:
            # two handles can be connected to the same GitHub account, and a
            # trailer naming the commit's own author is the noise this removed.
            if found is not None and found != author and found not in coauthors:
                coauthors.append(found)
    except Exception:  # noqa: BLE001 — same rule, narrower blast radius
        logger.warning(
            "could not resolve co-authors for topic %s", topic.id, exc_info=True
        )
    tasks: tuple[WorkItem, ...] = ()
    try:
        tasks = await work_items(session, card)
    except Exception:  # noqa: BLE001 — same rule again: a trailer, not a gate
        logger.warning(
            "could not resolve the work behind topic %s", topic.id, exc_info=True
        )
    return Attribution(handle, author, tuple(coauthors), tasks)


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


def read(project_id: uuid.UUID, topic_id: uuid.UUID) -> GitIdentity | None:
    """The remembered author for this topic. Synchronous and DB-free on purpose:
    the machine that commits reads it while launching a screen, with no session."""
    try:
        raw = identity_path(project_id, topic_id).read_text("utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
        return GitIdentity(str(data["name"]), str(data["email"]))
    except (ValueError, KeyError, TypeError):
        logger.warning("unreadable git identity for topic %s", topic_id)
        return None


def remember(project_id: uuid.UUID, topic_id: uuid.UUID, identity: GitIdentity) -> None:
    """Persist the author for this topic. Best-effort: a workspace that cannot
    hold the sidecar still commits, just under the 芝士 identity."""
    if read(project_id, topic_id) == identity:
        return  # unchanged — don't rewrite the file on every turn
    path = identity_path(project_id, topic_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"name": identity.name, "email": identity.email}),
            encoding="utf-8",
        )
    except OSError:
        logger.warning("could not persist git identity for topic %s", topic_id)


async def sync_for_topic(
    session: Any, topic: "Topic", *, task_id: uuid.UUID | None = None
) -> None:
    """Refresh the remembered author from the DB. Called once per turn — the
    connection can appear (someone links GitHub mid-project) or change, and a
    place created before this existed has no sidecar at all.

    Takes the place rather than a handle so that WHO the work belongs to is
    decided in one place (`requester_handle`) instead of at each call site —
    passing ``topic.created_by`` here is what left every 分身-dispatched thread
    committing as 芝士.

    The sidecar is keyed by the PLACE, because that is what the worktree is
    keyed by: two threads in one room commit as two different people when they
    belong to two different people."""
    handle = await requester_handle(session, topic, task_id=task_id)
    if not handle:
        return
    try:
        identity = await resolve_for_handle(session, handle)
    except Exception:  # noqa: BLE001 — authorship must never fail a turn
        logger.exception("could not resolve git identity for %s", handle)
        return
    if identity is not None:
        remember(topic.project_id, task_id or topic.id, identity)


def coauthored_by(identity: GitIdentity | None) -> str | None:
    """One `Co-authored-by:` line — the trailer GitHub reads when it decides who
    a commit belongs to. Squash-merging collapses a whole topic branch into ONE
    commit, so this is the only lever that credits a contributor who is not that
    commit's author on the thing that actually lands on main.

    None for 芝士 (拍板 2026-08-17): every commit on this platform is one she
    typed, so the trailer would be true of every change and therefore carry no
    information, and `cheese@zhishi.local` links to no GitHub account — it would
    only pollute the repo's contributor list. `Cheese-Topic:` in the PR body is
    already the traceable record of where a change came from."""
    if identity is None or identity == CHEESE_IDENTITY:
        return None
    return f"Co-authored-by: {identity.name} <{identity.email}>"
