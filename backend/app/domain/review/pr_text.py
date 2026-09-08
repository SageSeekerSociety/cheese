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


def task_trailer(topic: Topic, item: identity.WorkItem) -> str:
    """One `Cheese-Task:` line — which piece of work, which 分身 did it, and a
    URL that opens that work.

    The URL is the room's page with `?tab=overview&card=<task_id>` — the exact
    query the room writes when somebody clicks that piece of work, so following
    it lands where clicking lands. Both halves are needed: the page reads `card`
    to know WHICH work to drill into and `tab` to know which panel to be on, and
    a link with only `card` opens whichever tab the reader last had. That is the
    whole difference from `Cheese-Card`, which stays a bare id — an accept card
    has no route, so a URL built from one would look clickable and open nothing.

    The id is still greppable out of permanent history: `card=` is a fixed
    prefix in front of it, so `grep -o 'card=[0-9a-f-]*'` gets what
    `Cheese-Task: <uuid>` used to hand over directly.

    Squashed onto one line, always. A trailer block ends at the first line that
    is not a trailer, so a newline inside a task's title would not merely look
    untidy: it would cut every trailer after it out of the block git and GitHub
    read, and a title chosen to contain `Requested-by: someone` would forge one.
    Titles come from whoever dispatched the work, so this is the boundary where
    that stops being possible."""
    title = " ".join(item.title.split())
    if len(title) > MAX_TASK_TITLE:
        title = f"{title[: MAX_TASK_TITLE - 1]}…"
    # Still three whitespace-separated fields, so two splits still take the line
    # apart: a URL has no spaces in it, and the 分身 id keeps having its own
    # stripped out — one there would silently push the title into its place.
    subagent = "".join((item.subagent_id or "").split()) or NO_SUBAGENT
    where = f"{_room_url(topic)}?tab=overview&card={item.task_id}"
    return f"Cheese-Task: {where} {subagent} {title}".rstrip()


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


def _room_url(topic: Topic) -> str:
    """Where a reader can open this room. Base from configuration, never a
    literal: the same commit text is produced by every deployment."""
    from app.core.config import settings

    base = settings.frontend_url.rstrip("/")
    return f"{base}/projects/{topic.project_id}/topics/{topic.id}"


def pr_trailers(
    topic: Topic,
    decided_by: str,
    who: identity.Attribution | None = None,
    card: AcceptCard | None = None,
) -> str:
    """Render contribution roles and delivery links as one Git trailer block.

    Reporters and code contributors come only from explicitly declared tasks;
    ownership does not imply either role. Agent and task links identify the work."""
    lines = []
    requester = (who.handle if who else None) or topic.created_by
    # `Name <email>`, not a bare handle (#189). A handle names a string; an
    # address names a person — GitHub renders an avatar and a link for one it
    # recognises, and `git log --author` / `shortlog` can group by it. Somebody
    # who never connected GitHub gets the platform's own domain rather than a
    # fabricated GitHub address (`identity.platform_identity`): a degrade that
    # admits itself beats one that looks linkable and points at nobody.
    if requester:
        asker = identity.as_trailer(requester, who.requester if who else None)
        lines.append(f"Requested-by: {asker}")
    for reporter in who.reporters if who else ():
        lines.append(f"Reported-by: {reporter}")
    if decided_by:
        # Empty when the PR is being OPENED (pr_publish): nobody has accepted
        # yet, and `Reviewed-by:` with a blank or a merely-routed name would
        # claim a review that has not happened.
        seen_it = identity.as_trailer(decided_by, who.reviewer if who else None)
        lines.append(f"Reviewed-by: {seen_it}")
    # The ROOM is a URL (#189): a uuid in `git log` is a dead end unless the
    # reader already knows this platform's routes, and this line exists so that
    # somebody auditing a commit can get to where the change was made.
    lines.append(f"Cheese-Topic: {_room_url(topic)}")
    if card is not None:
        # The CARD stays a bare id, deliberately. There is no route that opens an
        # accept card: `?card=` on the room's page takes a TASK id
        # (TopicView.vue), so hanging the card's id off it would produce a link
        # that looks clickable and opens nothing — worse than an id, because an
        # id is honestly a lookup key while a dead link is a claim. Making it a
        # URL is a frontend change (a deep link that resolves an accept card),
        # not a string change here.
        lines.append(f"Cheese-Card: {card.id}")
    lines.append(f"Cheese-Agent: {topic_agent_handle(topic.id)}")
    lines.extend(task_trailer(topic, item) for item in (who.tasks if who else ()))
    coauthors = who.coauthors if who else ()
    credited = [line for line in map(identity.coauthored_by, coauthors) if line]
    # ONE block, no blank line before the co-authors. There used to be one, with
    # a comment claiming git wanted it; git wants the opposite. A blank line ENDS
    # a trailer block, and `git interpret-trailers --parse` reads only the LAST
    # one — so the separator did not group these trailers, it threw away every
    # trailer above it: on a change with a co-author, git saw `Co-authored-by`
    # and nothing else. Nothing caught it because the tests asked whether the
    # text contained the line, and it did; only git disagreed — which is why the
    # regression for this runs `git interpret-trailers` for real.
    lines.extend(credited)
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
