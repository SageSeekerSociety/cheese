"""Who a topic's commits are authored by.

Every commit the platform makes used to be authored by `芝士
<cheese@zhishi.local>` — an address that belongs to no GitHub account, so on
GitHub the work showed up as a grey unlinked name: no avatar, no link, no
contribution credit for the person who asked for it and approved it. This module
resolves the human behind a topic to a git identity GitHub *can* link, and
persists it next to the workspace so the synchronous snapshot path (which has no
DB session) can read it.

The address is GitHub's `<id>+<login>@users.noreply.github.com` form. It is the
only email guaranteed to resolve to the account: a user's real email may be
private, unverified, or simply different from the one they signed up with, and
any of those makes the commit unlinked again. The numeric id is what GitHub
matches on, which is why the login alone is not enough.

Who this names: the human the topic BELONGS TO — its roster owner, see
`requester_handle`. They asked for the change and they are the one accountable
for it; the agent typed it. That the agent typed it is not hidden — every such
commit rides a PR whose body carries `Cheese-Topic:` and whose subject 芝士
wrote, and the platform's own commits (repo init, upstream merges) keep the 芝士
identity because nobody asked for those.

One knob, not two: git carries author and committer separately, but jj 0.43 sets
both from JJ_USER/JJ_EMAIL and has no `--author`. So an attributed commit is
attributed wholly, and there is no place to record "committed by 芝士" on the
commit itself.
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

__all__ = [
    "CHEESE_EMAIL",
    "CHEESE_IDENTITY",
    "CHEESE_NAME",
    "GitIdentity",
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


async def requester_handle(session: Any, topic: "Topic") -> str | None:
    """The handle of the human a topic's work belongs to — the one name behind
    `Requested-by:`, the git author of its commits, and the account the PR is
    opened under.

    NOT ``topic.created_by``. A 分身 splits its sub-topics under its OWN handle
    (``cheese-<hex12>``, see ``identity.handles``), so on every split topic
    ``created_by`` names a robot that has no GitHub account, and every one of
    those attributions silently degraded: the PR opened as ``cheesex-app[bot]``,
    its body said ``Requested-by: cheese-a7a0268b``, and the commits carried no
    ``Co-authored-by`` at all (PR #500, #504). The roster already knows better —
    ``TopicService.split_to_subtopic`` walks a ladder (creator if human, else the
    parent room's owner, else the project's) precisely to seed a real human as
    the child's owner. This reads that answer instead of re-deriving it.

    Falls back to ``created_by``, which is what every caller used before: a room
    a human opened directly is unaffected (owner and creator are the same
    person), and a room where no human can be found behaves exactly as it does
    today rather than worse. Best-effort by construction — attribution must
    never be the reason a PR fails to open, so a broken roster read is logged
    and swallowed.
    """
    owner: str | None = None
    try:
        from app.domain.topic_membership.services import TopicMemberService

        owner = await TopicMemberService(session).owner_of(topic.id)
    except Exception:  # noqa: BLE001 — attribution never fails its caller
        logger.info("could not read roster owner for topic %s", topic.id, exc_info=True)
    if owner and not looks_like_agent_handle(owner):
        return owner
    return topic.created_by or None


def read(project_id: uuid.UUID, topic_id: uuid.UUID) -> GitIdentity | None:
    """The remembered author for this topic. Synchronous and DB-free on purpose:
    `snapshot_worktree` runs in a worker thread with no session."""
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


async def sync_for_topic(session: Any, topic: "Topic") -> None:
    """Refresh the remembered author from the DB. Called once per turn — the
    connection can appear (someone links GitHub mid-project) or change, and a
    topic created before this existed has no sidecar at all.

    Takes the topic rather than a handle so that WHO a topic belongs to is
    decided in one place (`requester_handle`) instead of at each call site —
    passing ``topic.created_by`` here is what left every 分身-split room
    committing as 芝士."""
    handle = await requester_handle(session, topic)
    if not handle:
        return
    try:
        identity = await resolve_for_handle(session, handle)
    except Exception:  # noqa: BLE001 — authorship must never fail a turn
        logger.exception("could not resolve git identity for %s", handle)
        return
    if identity is not None:
        remember(topic.project_id, topic.id, identity)


def coauthored_by(identity: GitIdentity | None) -> str | None:
    """The `Co-authored-by:` trailer GitHub reads when it decides who a commit
    belongs to. Squash-merging collapses a whole topic branch into ONE commit
    whose author GitHub picks for us, so this trailer is the only lever that
    reliably credits the human on the commit that actually lands on main."""
    if identity is None or identity == CHEESE_IDENTITY:
        return None
    return f"Co-authored-by: {identity.name} <{identity.email}>"
