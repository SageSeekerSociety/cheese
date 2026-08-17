"""The words a topic leaves on GitHub: the PR's title and body, and the subject
and body of the squash commit that lands on the default branch.

One module because they must not be able to disagree. They used to be written in
four places — the App publisher, the two-phase opener, the poller's merge, and
the force-merge — and each said something different about the same change: a
Chinese room name here, `采纳 topic/8f3a… → main (#7)` there, "验收人：alice" for
a body.
"""

from app.domain.review import commit_message
from app.domain.review.models import AcceptCard
from app.domain.topic.models import Topic
from app.domain.workspace import identity


def fallback_subject(topic: Topic) -> str:
    """The subject for a card filed without one — every card that predates
    `change_subject`, and any client that still doesn't send it.

    It is deliberately ugly. `chore: <话题标题>` is a truthful admission that
    nobody wrote a commit subject for this change, and it reads as clearly
    wrong in `git log`, which is the point: the fix is to file the card with
    `--subject`, not to make the fallback look presentable."""
    room = commit_message.MAX_SUBJECT - len("chore: ") - len(" (#0000)")
    title = topic.title or "untitled topic"
    trimmed = title if len(title) <= room else f"{title[: room - 1]}…"
    return f"chore: {trimmed}"


def change_subject(card: AcceptCard | None, topic: Topic) -> str:
    subject = commit_message.valid_subject(
        getattr(card, "change_subject", None) if card is not None else None
    )
    return subject or fallback_subject(topic)


def pr_trailers(
    topic: Topic,
    decided_by: str,
    author: identity.GitIdentity | None = None,
    requested_by: str | None = None,
) -> str:
    """Who this change belongs to, in the machine-readable form git and GitHub
    both already understand. Requested-by = 话题归属的真人
    (`identity.requester_handle`), Reviewed-by = 批准人 (AcceptCard.decided_by),
    Cheese-Topic = the room it came out of.

    `requested_by` is resolved by the caller because it needs a DB session and
    this module is pure. It defaults to `Topic.created_by`, which is what this
    used to read unconditionally — and which on a 分身-split room is the 分身's
    own `cheese-<hex12>` handle, not a person (PR #500, #504). Callers that can
    reach a session pass the real human in.

    `Co-authored-by` is the load-bearing one: squash-merging collapses the
    branch into ONE commit whose author GitHub picks, and a trailer is the only
    way to make sure the human who asked for the change is attached to it on
    GitHub — with an avatar, a link, and contribution credit — instead of the
    unlinkable `cheese@zhishi.local` the platform commits under."""
    lines = []
    requester = requested_by or topic.created_by
    if requester:
        lines.append(f"Requested-by: {requester}")
    if decided_by:
        # Empty when the PR is being OPENED (pr_publish): nobody has accepted
        # yet, and `Reviewed-by:` with a blank or a merely-routed name would
        # claim a review that has not happened.
        lines.append(f"Reviewed-by: {decided_by}")
    lines.append(f"Cheese-Topic: {topic.id}")
    coauthor = identity.coauthored_by(author)
    if coauthor:
        lines.append("")  # blank line: git wants trailers in one block, and
        lines.append(coauthor)  # Co-authored-by is read from the LAST block
    return "\n".join(lines)


def pr_body(
    topic: Topic,
    decided_by: str,
    card: AcceptCard | None = None,
    author: identity.GitIdentity | None = None,
    requested_by: str | None = None,
) -> str:
    """The PR description: what the change is for, then the trailers.

    The old body said only that 芝士 opened this on someone's behalf — true,
    and useless to a reviewer, who can see that from the PR's own metadata. The
    body a reviewer needs is the WHY, which is why `change_body` exists."""
    body = (getattr(card, "change_body", None) or "").strip() if card else ""
    parts = [body] if body else []
    parts.append(pr_trailers(topic, decided_by, author, requested_by))
    return "\n\n".join(parts)


def merge_commit_title(card: AcceptCard | None, topic: Topic, number: int) -> str:
    """The squash commit's title line — the subject of the ONE commit this
    topic leaves in the project's history."""
    return commit_message.merge_subject(change_subject(card, topic), number)


def merge_commit_message(
    topic: Topic,
    decided_by: str,
    card: AcceptCard | None = None,
    author: identity.GitIdentity | None = None,
    requested_by: str | None = None,
) -> str:
    """The squash commit's BODY: the why, then the trailers. The subject lives
    in `merge_commit_title`; repeating it here would put it in the commit
    twice."""
    return pr_body(topic, decided_by, card, author, requested_by)
