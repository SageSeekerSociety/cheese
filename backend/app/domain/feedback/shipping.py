"""Moving feedback to `deployed` when the release that fixes it reaches dev.

A commit names the reports it fixes with a line of its own:

    Fixes-feedback: FB-12, FB-15

The dev deploy (`.github/workflows/deploy-dev.yml`), once the release is up,
hands this module every commit between the release it replaced and the one it
just shipped (`scripts/ship_feedback.py`). Each named report moves to
`deployed` through `FeedbackService.set_status` — the same path the admin
button takes, so the timeline gets its entry and the submitter's unread count
moves exactly as it does for a person's change. The entry's `note` says which
PR it was, because nobody pressed anything: `by_handle` is NULL.

Main is squash-merged with the commit messages as the body, so a line in any
commit of the PR lands in the commit on main; the PR's own description does
not. The PR number comes from the `(#N)` GitHub appends to the squash title.

`FB-` rather than `#N`: on GitHub `#12` is issue 12 or PR 12, and a bare
`Fixes #12` closes that issue.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.feedback.models import Feedback, FeedbackStatus
from app.domain.feedback.services import FeedbackService

# At the very start of the line, as git writes a trailer. An indented line is a
# quotation: a commit message that shows the syntax as an example (this
# module's own commit did) must not move the reports its example names.
_LINE = re.compile(r"^fixes-feedback:(.*)$", re.IGNORECASE | re.MULTILINE)
_ITEM = re.compile(r"\bFB-(\d+)\b", re.IGNORECASE)
_PR = re.compile(r"\(#(\d+)\)\s*$")


def feedback_refs(message: str) -> list[int]:
    """The report numbers a commit message says it fixes, in order, once each."""
    found: list[int] = []
    for line in _LINE.findall(message):
        for number in _ITEM.findall(line):
            if int(number) not in found:
                found.append(int(number))
    return found


def pull_request_of(message: str) -> int | None:
    """The PR a squash commit came from, read off its title."""
    title = message.split("\n", 1)[0]
    match = _PR.search(title)
    return int(match.group(1)) if match else None


@dataclass(frozen=True)
class Outcome:
    display_no: int
    #: `deployed`, `already_deployed` or `not_found`.
    result: str
    link: str


async def mark_deployed(session: AsyncSession, display_no: int, *, note: str) -> str:
    """`FB-<display_no>` is live: `deployed`, `already_deployed` or `not_found`.

    `already_deployed` writes nothing (`set_status`'s no-second-entry rule);
    `not_found` covers a deleted report as well as a mistyped number.
    """
    row = (
        await session.execute(
            select(Feedback).where(
                Feedback.display_no == display_no, Feedback.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return "not_found"
    if row.status == FeedbackStatus.deployed:
        return "already_deployed"
    await FeedbackService(session).set_status(
        row.id, FeedbackStatus.deployed, by_handle=None, note=note
    )
    return "deployed"


async def ship(
    session: AsyncSession, commits: list[dict], *, repository_url: str
) -> list[Outcome]:
    """Mark every report the given commits fix as deployed.

    `commits` is `[{"sha": ..., "message": ...}]`, oldest first — what GitHub's
    compare endpoint returns. A report named by two commits moves once, on the
    first; the second finds it already there.
    """
    outcomes: list[Outcome] = []
    for commit in commits:
        message = commit["message"]
        pr = pull_request_of(message)
        if pr is not None:
            link, label = f"{repository_url}/pull/{pr}", f"PR #{pr}"
        else:
            link = f"{repository_url}/commit/{commit['sha']}"
            label = f"提交 {commit['sha'][:7]}"
        for number in feedback_refs(message):
            note = f"已由 {label} 修复并上线：{link}"
            result = await mark_deployed(session, number, note=note)
            outcomes.append(Outcome(number, result, link))
    return outcomes
