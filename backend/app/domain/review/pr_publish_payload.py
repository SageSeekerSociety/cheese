"""What a card's PR says, and the two writes that record the outcome.

This is the forge-facing text half of `review/pr_publish.py`, cut out of it on
2026-10-16. It holds the PR one card is about, and what that PR says:

- the PR the task already has (`_open_pr_of_task`) and the text it carries
  (`_pr_text`, `draft_title`, `is_draft_title`, `sync_pr_text`,
  `PR_OPEN_FAILED_PREFIX`);
- the two writes that make the outcome visible on the card (`record_pr`,
  `_record_failure`).

**What is not here is the sweep.** `_draft_pr_for_one_task` and
`_open_draft_for_task` stayed in the facade, along with everything else that
reads `enabled`, `branch_head`, `proposal_client` or `status_client`: tests
replace those names on `app.domain.review.pr_publish`, and a body over here
would read this module's own copy while the test replaced the other — the
replacement would quietly stop taking effect. Nothing here imports the facade
back: that cycle is what this shape exists to avoid
(`review/github_pr/__init__.py` is the same shape, and says the same thing).
"""

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.repository import identity
from app.domain.review import credit, notes, pr_text
from app.domain.review.forgejo_pr import ForgejoPRClient
from app.domain.review.github_pr import GitHubPRClient
from app.domain.review.repositories import AcceptCardRepository
from app.domain.room_task.models import Task
from app.domain.room_task.place import PlaceResolver

#: The forge log channel is the sub-system's name, not this file's: the split
#: moved no line of a log a person greps for.
logger = logging.getLogger("cheesex.pr_publish")


#: 开 PR 失败落卡 (#362 修法 1)。一张开 PR 失败的卡必须和「PR 还在路上」的卡长得
#: 明显不同——这条前缀就是那个不同：失败原因直接写在 note 上，而不是只进 logger。
#: 采纳现场的补开（AcceptService._publish_pr_for_accept）就是它的重试路径；重试
#: 开出 PR 后 `record_pr` 会把这条 note 清掉。
PR_OPEN_FAILED_PREFIX = "开 PR 失败"

#: 草稿 PR 顶着的占位标题。No `Reviewed-by` and no card: nobody has accepted,
#: and the subject this change will land under is not written until somebody
#: files a card — this prefix says both. 递卡 replaces it (`sync_pr_text`), and
#: so does `AcceptService.mark_ready` for the tasks marked ready first: the
#: full `CI required` that the ready flag starts reads the PR's title, and a
#: repository checking its own titles reads `WIP:` there and fails a change
#: everything else passed.
DRAFT_TITLE_PREFIX = "WIP: "


def draft_title(fallback: str) -> str:
    """The placeholder a task's draft PR opens under."""
    return f"{DRAFT_TITLE_PREFIX}{fallback}"[:255]


def is_draft_title(title: str | None) -> bool:
    """Whether a PR's title is still the platform's placeholder — the only
    title the platform gets to overwrite.

    A subject 递卡 wrote, or one somebody typed on GitHub, does not start with
    the prefix, so recognising the placeholder is also what keeps either of
    them from being clobbered."""
    return (title or "").startswith(DRAFT_TITLE_PREFIX)


async def _open_pr_of_task(
    client: GitHubPRClient | ForgejoPRClient, task: Task
) -> dict | None:
    """The PR the task already has, while it is still open on the task's branch.

    The card rides this PR rather than whatever `open_pr` returns: `open_pr`
    adopts only a PR with the same head AND base, and a person may have moved
    the task's PR onto another base on the forge. Asked with the task's old
    base, it opened a second PR for the same branch, and the card and the task
    then named different PRs (FB-96)."""
    if task.pr_number is None:
        return None
    pr = await client.pr_view(task.pr_number)
    head = pr.get("head") or {}
    if pr.get("state") != "open" or head.get("ref") != task.branch_name:
        return None
    return pr


async def sync_pr_text(
    client: GitHubPRClient | ForgejoPRClient, pr: dict, *, title: str, body: str
) -> None:
    """Make the PR on GitHub say what `title`/`body` say — and only then.

    Skipping the write when it would change nothing is not an optimisation: a
    PATCH to a PR is an edit event on GitHub, and re-issuing it on every accept
    poll would fill the timeline with edits that changed no character.
    """
    if pr.get("title") == title and (pr.get("body") or "") == body:
        return
    number = pr.get("number")
    if number is None:
        return
    updated = await client.update_pr(int(number), title=title, body=body)
    pr.update(updated)


async def owner_token(session: AsyncSession, topic, task_id) -> str | None:
    """The credential the task's PR is opened with when the project credits the
    task owner: the owner's GitHub sign-in connection. None otherwise, and None
    whenever anything goes wrong — then the App opens it."""
    try:
        # deferred-import: tests replace this name on app.domain.project.services
        from app.domain.project.services import ProjectService

        project = await ProjectService(session).get(topic.project_id)
        settings = (project.settings or {}) if project else {}
        if identity.credit_mode(settings) != identity.CREDIT_OWNER:
            return None
        handle = await identity.owner_handle(session, task_id)
        if not handle:
            return None
        # deferred-import: tests replace this name on app.domain.oauth.services
        from app.domain.oauth.services import get_github_user_token_for_handle

        return await get_github_user_token_for_handle(session, handle)
    except Exception:  # an attribution nicety, never why a PR fails to open
        logger.info("no owner token for task %s", task_id, exc_info=True)
        return None


async def pr_body_for(
    session: AsyncSession,
    topic,
    *,
    card=None,
    task_id=None,
    opened_by: str | None,
) -> str:
    """The PR body credited for a PR opened by `opened_by`. No approver yet:
    `Reviewed-by` is written onto the squash commit at merge time, by whoever
    actually clicks."""
    who = await credit.attribution(
        session, topic, card=card, task_id=task_id, opened_by=opened_by
    )
    return pr_text.pr_body(topic, "", card, who)


async def _pr_text(
    session: AsyncSession,
    *,
    card_id: uuid.UUID,
    topic_id: uuid.UUID,
    branch: str,
) -> tuple[str, str | None, str]:
    """The card's PR title, the owner's token to open it with, and a first
    body. The PR's own words come from the card's change summary
    (`cheese_card(action=request)`'s subject and body).

    Same builders the merge path uses (`review/pr_text.py`), on purpose: the
    PR a reviewer reads and the squash commit that lands on main must not be
    able to say two different things about the same change."""

    place = await PlaceResolver(session).resolve(topic_id)
    card = await AcceptCardRepository(session).get(card_id)
    if place is None:
        return branch, None, ""
    topic = place.room
    who = await identity.attribution(session, topic, card=card)
    # deferred-import: tests replace this name on app.domain.project.forge
    from app.domain.project.forge import ensure_author_email

    if who.author:
        await ensure_author_email(topic.project_id, session, who.author.email)
    token = await owner_token(session, topic, getattr(card, "task_id", None))
    # Only for a PR this call creates; the caller rewrites it once GitHub has
    # said who the PR ended up opened by.
    body = pr_text.pr_body(topic, "", card, who)
    return pr_text.change_subject(card, topic), token, body


async def record_pr(
    session_factory: async_sessionmaker, *, card_id: uuid.UUID, pr: dict
) -> None:
    """Write the opened PR onto the card AND onto the task it delivers.

    Both, because both answer questions somebody asks: the card is what a
    reviewer opens, and the task is what the draft-PR sweep consults to know
    this task already has one. Writing only the card left a delivered task
    looking, to the sweep, like a task that had never had a PR.

    Clears a `PR_OPEN_FAILED_PREFIX` note from an earlier failed publish — the
    card rides a PR now, and a stale「开 PR 失败」would contradict the pr_number
    sitting next to it.
    """
    # deferred-import: tests replace this name on app.domain.room_task.services
    from app.domain.room_task.services import TaskService

    async with session_factory() as session:
        card = await AcceptCardRepository(session).get(card_id)
        if card is None:
            logger.error("PR recorded nowhere: card %s vanished", card_id)
            return
        number = int(pr["number"])
        url = str(pr.get("html_url") or "")[:255] or None
        card.pr_number = number
        card.pr_url = url
        if card.note_code is notes.NoteCode.pr_open_failed:
            notes.clear(card)
        if card.task_id is not None:
            tasks = TaskService(session)
            task = await tasks.get(card.task_id)
            # The task follows the card: it names the PR the card rides.
            if task is not None and task.pr_number != number:
                await tasks.record_pr(task, number=number, url=url)
        await session.commit()


async def _record_failure(
    session_factory: async_sessionmaker, *, card_id: uuid.UUID, exc: BaseException
) -> None:
    """#362 修法 1: a failed publish lands ON THE CARD, not only in a log no
    one reads. A PR-less card used to be indistinguishable from one whose PR
    simply hadn't landed yet, and the accept path then slid into the local
    merge without anyone knowing the PR step had failed at all. Accepting the
    card retries the publish (AcceptService._publish_pr_for_accept), which
    closes the loop: fail visibly here, retry at accept, and the retry's own
    outcome replaces this note. Best-effort: a DB hiccup here must not raise
    out of the background task."""

    note = (
        f"{PR_OPEN_FAILED_PREFIX}（{str(exc)[:300]}）。目前没有 PR，"
        "采纳时会重新开 PR；如果仍然开不了，采纳会停止，不会直接推送改动。"
    )[:2000]
    try:
        async with session_factory() as session:
            card = await AcceptCardRepository(session).get(card_id)
            if card is None:
                return
            notes.record(card, notes.NoteCode.pr_open_failed, note)
            await session.commit()
    except Exception:
        logger.exception("PR publish failure not recorded on card %s", card_id)
