"""The words a topic leaves on GitHub: the PR's title and body, and the subject
and body of the squash commit that lands on the default branch.

One module because they must not be able to disagree. They used to be written in
four places — the App publisher, the two-phase opener, the poller's merge, and
the force-merge — and each said something different about the same change: a
Chinese room name here, `采纳 topic/8f3a… → main (#7)` there, "验收人：alice" for
a body.
"""

import re

from app.core.config import settings
from app.domain.repository import identity
from app.domain.review import commit_message
from app.domain.review.models import AcceptCard
from app.domain.topic.models import Topic

#: The width of `AcceptCard.change_subject`, the bound every filed subject
#: already meets at the API.
MAX_FALLBACK = 255

#: The trailers this module writes. A card body that ends with any of them —
#: an agent pasting its own commit's trailers — has them dropped, so a credit
#: is written once and only by `pr_trailers`.
_OWN_TRAILERS = frozenset(
    {"co-authored-by", "reported-by", "reviewed-by", "requested-by", "link"}
)
_TRAILER_LINE = re.compile(r"^([A-Za-z][A-Za-z0-9-]*):\s+\S")


def task_link(topic: Topic, item: identity.WorkItem) -> str:
    """The address of one task: its own page, under its room
    (`/projects/<p>/topics/<room>/tasks/<task>`), the page clicking the task
    opens."""
    return f"{_room_url(topic)}/tasks/{item.task_id}"


def fallback_subject(topic: Topic) -> str:
    """The subject for a card that has none — now only the rows filed before
    `change_subject` existed, whose column is NULL.

    Since 2026-08-17 no NEW card can reach this: `AcceptService.create_card`
    refuses a card without a subject. This stays for the history already in the
    table, which is also why it must not be "cleaned up" — deleting it breaks
    the PR title and merge subject of every pre-existing card.

    The topic title, untouched in format: the platform does not know the
    repository's commit convention, so it does not dress the line up in one.
    Trimmed to the card column's width, which also keeps it under GitHub's
    256-character limit on a PR title — a topic title can run to 300."""
    title = " ".join((topic.title or "").split()) or "untitled topic"
    return title if len(title) <= MAX_FALLBACK else f"{title[: MAX_FALLBACK - 1]}…"


def change_subject(card: AcceptCard | None, topic: Topic) -> str:
    subject = commit_message.valid_subject(
        getattr(card, "change_subject", None) if card is not None else None
    )
    return subject or fallback_subject(topic)


def _room_url(topic: Topic) -> str:
    """Where a reader can open this room. Base from configuration, never a
    literal: the same commit text is produced by every deployment."""

    base = settings.frontend_url.rstrip("/")
    return f"{base}/projects/{topic.project_id}/topics/{topic.id}"


def pr_trailers(
    topic: Topic,
    decided_by: str,
    who: identity.Attribution | None = None,
    kept: tuple[str, ...] = (),
) -> str:
    """The trailer block: credits, then where the change came from.

    Only keys git and GitHub already know. `Co-authored-by` is the one GitHub
    shows (an avatar, a contribution); `Reported-by` and `Reviewed-by` are the
    kernel's; `Link` is the kernel's line for where a change was discussed. The
    commit's author is not here: GitHub takes it from whoever opened the PR.

    `kept` are trailer lines from the card body this module does not write
    (`Fixes-feedback:`), put in the same block so git reads them."""
    lines = list(kept)
    for reporter in who.reporters if who else ():
        lines.append(f"Reported-by: {reporter}")
    if decided_by:
        # Empty when the PR is being OPENED: nobody has accepted yet, and
        # `Reviewed-by:` with a blank or a merely-routed name would claim a
        # review that has not happened.
        seen_it = identity.as_trailer(decided_by, who.reviewer if who else None)
        lines.append(f"Reviewed-by: {seen_it}")
    tasks = who.tasks if who else ()
    if tasks:
        lines.extend(f"Link: {task_link(topic, item)}" for item in tasks)
    else:
        lines.append(f"Link: {_room_url(topic)}")
    coauthors = who.coauthors if who else ()
    # ONE block, no blank line before the co-authors. A blank line ENDS a
    # trailer block, and `git interpret-trailers --parse` reads only the LAST
    # one, so a separator would throw away every trailer above it.
    lines.extend(line for line in map(identity.coauthored_by, coauthors) if line)
    return "\n".join(lines)


def _written_here(line: str) -> bool:
    found = _TRAILER_LINE.match(line)
    key = found.group(1).lower() if found else ""
    # `Cheese-*` is what this module used to write; a body copied from an
    # older commit still carries it.
    return key in _OWN_TRAILERS or key.startswith("cheese-")


def split_trailers(body: str) -> tuple[str, tuple[str, ...]]:
    """`body` without its closing trailer paragraph, and the lines of that
    paragraph this module does not write itself.

    A paragraph is a trailer paragraph when every line in it is `Key: value`,
    the rule git applies. Lines with a key `pr_trailers` writes are dropped:
    those credits are decided by the platform, and a pasted copy would repeat
    them above the real block."""
    head, sep, last = body.replace("\r\n", "\n").rstrip().rpartition("\n\n")
    lines = [line.strip() for line in last.splitlines() if line.strip()]
    if not lines or not all(_TRAILER_LINE.match(line) for line in lines):
        return body, ()
    kept = tuple(line for line in lines if not _written_here(line))
    return (head if sep else "").rstrip(), kept


#: The line that starts an independent review's result in a card body. The seat
#: delivering the work pastes the result after its own sections; render.py writes
#: this line first. The patrol board reads the line from the card, while review
#: results stay off GitHub.
_REVIEW_BLOCK = re.compile(r"^独立 review：", re.MULTILINE)


def without_review_block(body: str) -> str:
    """`body` up to the line that starts a review result, that line excluded.

    Everything from that line on is dropped too. The card body is mirrored into
    the PR body and the squash commit, so this is the one place that keeps the
    review result from reaching either."""
    found = _REVIEW_BLOCK.search(body)
    return body[: found.start()].rstrip() if found else body


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
    body, kept = split_trailers(without_review_block(body))
    parts = [body] if body else []
    parts.append(pr_trailers(topic, decided_by, who, kept))
    return "\n\n".join(parts)


def pr_title_matches_subject(
    card: AcceptCard | None, topic: Topic, pr_title: str | None
) -> bool:
    """Whether GitHub's PR title is still the line this card would land.

    One string, three readers: the platform WRITES the PR title from the card's
    subject, the repository's own checks JUDGE that PR title (an edit re-runs
    their `pull_request` workflows), and the squash commit LANDS the same
    subject (`merge_commit_title`). A title edited on GitHub by hand — or a
    card whose subject moved without the PR following it — makes the commit
    that would land something the repository never checked. The platform does
    not get to overrule a repository's checks, so both merge paths call this
    first and stop when it is False.

    Nothing here interprets the title: whether it is a good title, in the
    right language, in the repository's own convention, is the repository's
    business, decided by its own CI on the PR. This only asks whether the two
    readers are looking at one string.

    An absent or empty `pr_title` (a fake or older payload) is NOT a match:
    an unreadable title is "cannot confirm", and confirming is the whole point.
    """
    title = (pr_title or "").strip()
    return bool(title) and title == change_subject(card, topic).strip()


def merge_commit_title(card: AcceptCard | None, topic: Topic, number: int) -> str:
    """The squash commit's title line — the subject of the ONE commit this
    topic leaves in the project's history. GitHub only auto-appends `(#N)` to
    the DEFAULT title it derives from the repo's `squash_merge_commit_title`
    setting; an explicit `commit_title` replaces that wholesale, so the number
    is appended here or the `… (#213)` history style breaks."""
    return f"{change_subject(card, topic)} (#{number})"


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
