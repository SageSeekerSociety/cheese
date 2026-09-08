"""The words a topic leaves on GitHub: the PR's title and body, and the subject
and body of the squash commit that lands on the default branch.

One module because they must not be able to disagree. They used to be written in
four places — the App publisher, the two-phase opener, the poller's merge, and
the force-merge — and each said something different about the same change: a
Chinese room name here, `采纳 topic/8f3a… → main (#7)` there, "验收人：alice" for
a body.
"""

from app.domain.identity.handles import topic_agent_handle
from app.domain.review import commit_message
from app.domain.review.models import AcceptCard
from app.domain.topic.models import Topic
from app.domain.workspace import identity

#: What `Cheese-Task:` writes where a 分身 id would go, for work no worker was
#: ever bound to. A placeholder rather than a shorter line, so every one of these
#: trailers has the same three fields and can be split on whitespace twice.
NO_SUBAGENT = "-"

#: How much of a task's title a trailer carries. Titles run to 300 characters and
#: a commit message is read in an 80-column terminal; the id and the 分身 in front
#: of it are what identify the work, the title is there to be recognised.
MAX_TASK_TITLE = 120


def task_trailer(item: identity.WorkItem) -> str:
    """One `Cheese-Task:` line — which piece of work, and which 分身 did it.

    Squashed onto one line, always. A trailer block ends at the first line that
    is not a trailer, so a newline inside a task's title would not merely look
    untidy: it would cut every trailer after it out of the block git and GitHub
    read, and a title chosen to contain `Requested-by: someone` would forge one.
    Titles come from whoever dispatched the work, so this is the boundary where
    that stops being possible."""
    title = " ".join(item.title.split())
    if len(title) > MAX_TASK_TITLE:
        title = f"{title[: MAX_TASK_TITLE - 1]}…"
    # No spaces in the id either: it is the second of three whitespace-separated
    # fields, so one would silently push the title into the 分身's place.
    subagent = "".join((item.subagent_id or "").split()) or NO_SUBAGENT
    return f"Cheese-Task: {item.task_id} {subagent} {title}".rstrip()


def fallback_subject(topic: Topic) -> str:
    """The subject for a card that has none — now only the rows filed before
    `change_subject` existed, whose column is NULL.

    Since 2026-08-17 no NEW card can reach this: `AcceptService.create_card`
    refuses a card without a subject. This stays for the history already in the
    table, which is also why it must not be "cleaned up" — deleting it breaks
    the PR title and merge subject of every pre-existing card.

    It is deliberately ugly. `chore: <话题标题>` is a truthful admission that
    nobody wrote a commit subject for this change, and it reads as clearly
    wrong in `git log`, which is the point."""
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
    who: identity.Attribution | None = None,
    card: AcceptCard | None = None,
) -> str:
    """Who this change belongs to, in the machine-readable form git and GitHub
    both already understand. Requested-by = 话题归属的真人
    (`identity.requester_handle`), Reviewed-by = 批准人 (AcceptCard.decided_by),
    Cheese-Topic = the room it came out of, Cheese-Card = the accept card that
    delivered it (#189) — the room says where it was made, the card says which
    delivery of that room's work this commit is.

    `Cheese-Agent` and `Cheese-Task` answer the other half of #189: WHICH 芝士
    wrote this. Nothing else in the commit can say — the git author is the human
    the work belongs to, and `Co-authored-by: Claude Fable 5` is on every commit
    Claude Code writes for anyone, anywhere, so between them a reader learns who
    asked and what model typed, and nothing about which instance of this platform
    did it or which piece of work it was. `Cheese-Agent` is the room's 分身
    handle, the same name it posts under and holds a token as; `Cheese-Task` is
    one line per piece of work the card DECLARED it delivers, naming the worker
    that did it — a card that declared none writes none, because a name that is
    merely plausible is worse in permanent history than no name at all.

    The agent handle is derived here rather than passed in because it is a pure
    function of the room (`topic_agent_handle`) — routing it through the caller's
    DB resolution would only create a way for the line to go missing. The task
    lines cannot be: they are rows, so they arrive on `who`.

    `who` is resolved by the caller (`identity.attribution`) because it needs a DB
    session and this module is pure — as ONE object, so the requester and the
    co-authors cannot come from two different resolutions and name the same person
    twice. None (a caller with no session to resolve with) falls back to
    `Topic.created_by`, which is what this used to read unconditionally — and which
    on a 分身-split room is the 分身's own `cheese-<hex12>` handle, not a person
    (PR #500, #504).

    `Co-authored-by` names the contributors who are NOT this commit's author —
    normally nobody, and then no such line is written. It used to name the author
    itself on every change, which was pure noise: a room has one git identity, so
    the trailer pointed at the person the commit was already authored by. It earns
    its place when a room changed hands, where the work is attributed to whoever
    drove it and the original requester would otherwise vanish from the history —
    squash-merging collapses the branch into ONE commit, so a trailer is the only
    place a second contributor survives with an avatar, a link and credit."""
    lines = []
    requester = (who.handle if who else None) or topic.created_by
    if requester:
        lines.append(f"Requested-by: {requester}")
    if decided_by:
        # Empty when the PR is being OPENED (pr_publish): nobody has accepted
        # yet, and `Reviewed-by:` with a blank or a merely-routed name would
        # claim a review that has not happened.
        lines.append(f"Reviewed-by: {decided_by}")
    lines.append(f"Cheese-Topic: {topic.id}")
    if card is not None:
        lines.append(f"Cheese-Card: {card.id}")
    lines.append(f"Cheese-Agent: {topic_agent_handle(topic.id)}")
    lines.extend(task_trailer(item) for item in (who.tasks if who else ()))
    coauthors = who.coauthors if who else ()
    credited = [line for line in map(identity.coauthored_by, coauthors) if line]
    if credited:
        lines.append("")  # blank line: git wants trailers in one block, and
        lines.extend(credited)  # Co-authored-by is read from the LAST block
    return "\n".join(lines)


def pr_body(
    topic: Topic,
    decided_by: str,
    card: AcceptCard | None = None,
    who: identity.Attribution | None = None,
) -> str:
    """The PR description: what the change is for, then the trailers.

    The old body said only that 芝士 opened this on someone's behalf — true,
    and useless to a reviewer, who can see that from the PR's own metadata. The
    body a reviewer needs is the WHY, which is why `change_body` exists."""
    body = (getattr(card, "change_body", None) or "").strip() if card else ""
    parts = [body] if body else []
    parts.append(pr_trailers(topic, decided_by, who, card))
    return "\n\n".join(parts)


def merge_commit_title(card: AcceptCard | None, topic: Topic, number: int) -> str:
    """The squash commit's title line — the subject of the ONE commit this
    topic leaves in the project's history."""
    return commit_message.merge_subject(change_subject(card, topic), number)


def merge_commit_message(
    topic: Topic,
    decided_by: str,
    card: AcceptCard | None = None,
    who: identity.Attribution | None = None,
) -> str:
    """The squash commit's BODY: the why, then the trailers. The subject lives
    in `merge_commit_title`; repeating it here would put it in the commit
    twice."""
    return pr_body(topic, decided_by, card, who)


def local_merge_commit_message(
    topic: Topic,
    decided_by: str,
    card: AcceptCard | None = None,
    who: identity.Attribution | None = None,
) -> str:
    """The WHOLE commit message for the platform forge's squash (#363): subject,
    blank line, then the same body the GitHub lane writes. One string because the
    local merge takes one `-m`, and no `(#N)` because there is no PR whose number
    it could truthfully cite."""
    return "\n\n".join(
        [change_subject(card, topic), pr_body(topic, decided_by, card, who)]
    )
