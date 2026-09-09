"""每一批活在 GitHub 上的那个 PR —— 开出来、改文案、翻出 draft。

Two moments put a PR here, and they are not alternatives:

- **有东西就有 PR** (#718 拍板①). The task's FIRST COMMIT opens a DRAFT PR, with
  no card and nobody asked to accept anything yet — draft is GitHub's word for
  进行中. `sweep_draft_prs` does it, and its docstring says why the platform has
  to OBSERVE that commit rather than hook it.
- **递卡** dispatches `open_pr_for_card` fire-and-forget: it pushes the branch,
  ADOPTS the draft PR that is already there (or opens one, for a task that had
  none), rewrites its title and body from the card, and takes it out of draft —
  递卡 means「请人来看」. The background dispatch is best-effort: any failure
  leaves the card PR-less, visibly (`PR_OPEN_FAILED_PREFIX`).

Acceptance does NOT fall back to a local merge for a PR-less card — the accept
path retries the publish synchronously via `open_pr_for_card` and stops, visibly,
if opening the PR still fails (AcceptService._publish_pr_for_accept): a merge
commit direct-pushed to main with no PR is exactly what #296 exists to end.

Same task-reference pattern as review/gate.py.
"""

import asyncio
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.domain.agent.github_app import github_app_tokens_for_project
from app.domain.review import notes
from app.domain.review.github_pr import GitHubPRClient, parse_github_repo
from app.domain.workspace import service as ws

logger = logging.getLogger("cheesex.pr_publish")

#: 开 PR 失败落卡 (#362 修法 1)。一张开 PR 失败的卡必须和「PR 还在路上」的卡长得
#: 明显不同——这条前缀就是那个不同：失败原因直接写在 note 上，而不是只进 logger。
#: 采纳现场的补开（AcceptService._publish_pr_for_accept）就是它的重试路径；重试
#: 开出 PR 后 `record_pr` 会把这条 note 清掉。
PR_OPEN_FAILED_PREFIX = "开 PR 失败"

_TASKS: set[asyncio.Task] = set()


def enabled() -> bool:
    """Cheap pre-check: the GitHub App is configured at all. The per-project
    eligibility (which installation, upstream is a GitHub https remote) is
    resolved in the task itself — it needs the DB and a subprocess."""
    return bool(settings.github_app_id) and bool(settings.github_app_private_key_path)


def dispatch(
    session_factory: async_sessionmaker,
    *,
    card_id: uuid.UUID,
    topic_id: uuid.UUID,
    project_id: uuid.UUID,
) -> None:
    """Start the PR publication in the background; returns immediately."""
    task = asyncio.create_task(
        _run(
            session_factory,
            card_id=card_id,
            topic_id=topic_id,
            project_id=project_id,
        )
    )
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def _run(
    session_factory: async_sessionmaker,
    *,
    card_id: uuid.UUID,
    topic_id: uuid.UUID,
    project_id: uuid.UUID,
) -> None:
    try:
        pr = await _publish(
            session_factory,
            card_id=card_id,
            topic_id=topic_id,
            project_id=project_id,
        )
    except Exception as exc:  # noqa: BLE001 — card stays PR-less, but never silently (#362)
        logger.exception("PR publication failed for card %s", card_id)
        await _record_failure(session_factory, card_id=card_id, exc=exc)
        return
    if pr is None:
        return
    await record_pr(session_factory, card_id=card_id, pr=pr)


async def _publish(
    session_factory: async_sessionmaker,
    *,
    card_id: uuid.UUID,
    topic_id: uuid.UUID,
    project_id: uuid.UUID,
) -> dict | None:
    async with session_factory() as session:
        return await open_pr_for_card(
            session, card_id=card_id, topic_id=topic_id, project_id=project_id
        )


async def open_pr_for_card(
    session: AsyncSession,
    *,
    card_id: uuid.UUID,
    topic_id: uuid.UUID,
    project_id: uuid.UUID,
) -> dict | None:
    """Push the topic branch and open (or adopt) its App PR — the shared core
    of the fire-and-forget publish above and the accept-time publish for
    legacy PR-less cards (AcceptService._publish_pr_for_accept).

    Returns the PR json, or None when the PR path is NOT APPLICABLE to this
    card: no App installation for the project, a non-GitHub upstream, or a
    discussion-only topic with no branch to put in a PR. Raises when opening
    the PR FAILED — the two callers treat that differently (background: log
    and leave the card PR-less; accept: stop the accept, never direct-merge).
    Only ever reads through `session`; the card row is written by whoever
    owns it (`record_pr` below, or the accept transaction)."""
    # #192: the installation is resolved from this card's project, not a global.
    tokens = await github_app_tokens_for_project(project_id, session)
    if tokens is None:
        return None
    upstream = await asyncio.to_thread(ws.get_upstream, project_id)
    parsed = parse_github_repo(upstream)
    if parsed is None:
        return None  # not a GitHub https upstream — PR path not applicable
    owner, repo_name = parsed
    from app.domain.review.repositories import AcceptCardRepository
    from app.domain.room_task.services import TaskService

    card = await AcceptCardRepository(session).get(card_id)
    if card is None or card.task_id is None:
        return None
    task = await TaskService(session).require_in_room(topic_id, card.task_id)
    if not await asyncio.to_thread(ws.topic_branch_exists, project_id, task.id):
        return None  # discussion-only topic — nothing a PR could carry

    token, _ = await tokens.write_token()
    branch = await asyncio.to_thread(ws.push_topic_branch, project_id, task.id, token)
    base = task.base_branch
    if base is None:
        raise ValueError("Task has no target branch")

    title, body = await _pr_text(
        session, card_id=card_id, topic_id=topic_id, branch=branch
    )
    client = GitHubPRClient(owner, repo_name, tokens)
    pr = await client.open_pr(
        head=branch,
        base=base,
        title=title,
        body=body,
        # Open it as the person whose work it is, not as the bot — see
        # `GitHubPRClient.open_pr`. Push above still uses the App token: pushing
        # is not attributed to anyone, and the App's write access is the one
        # thing here that is guaranteed to work.
        as_user_token=await _requester_token(session, topic_id),
    )
    # The PR very often already exists by now: the task's draft PR was opened
    # at its first commit (#718 拍板①) and `open_pr` adopts it rather than
    # failing on GitHub's "already exists". An adopted PR still carries the
    # placeholder words the draft opened with, so the card's own subject/body
    # has to be written onto it — otherwise the reviewer reads 「WIP: 房间名」
    # while the squash commit says something else entirely.
    await sync_pr_text(client, pr, title=title, body=body)
    # 递卡的语义就是「请人来看」，所以卡一递出去，PR 就不再是 draft (#718 拍板①)。
    # Not best-effort: a card that says 等验收 while GitHub still says 草稿 is a
    # delivery nobody can review, and nothing else would ever say so.
    if pr.get("draft") and pr.get("node_id"):
        await client.mark_ready_for_review(str(pr["node_id"]))
        pr["draft"] = False
    logger.info(
        "PR #%s ready for card %s (%s)", pr.get("number"), card_id, pr.get("html_url")
    )
    return pr


async def sync_pr_text(
    client: GitHubPRClient, pr: dict, *, title: str, body: str
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


async def sweep_draft_prs(session_factory: async_sessionmaker) -> dict[str, int]:
    """Open draft PRs for task branches with commits and no existing PR.

    Commits arrive from different executors through Git; the shared PR poller
    observes them after synchronization. Delay is one poll interval plus the
    time spent on earlier tasks in this pass. Each task uses its own session
    so a failed write cannot poison the remaining deliveries.
    """
    from app.domain.room_task.services import TaskService

    counts = {"opened": 0, "skipped": 0, "failed": 0}
    await retarget_completed_dependencies(session_factory)
    if not enabled():
        return counts
    async with session_factory() as session:
        wanted = [t.id for t in await TaskService(session).open_without_pr()]
    for task_id in wanted:
        try:
            async with session_factory() as session:
                opened = await _draft_pr_for_one_task(session, task_id)
                await session.commit()
        except Exception:  # noqa: BLE001 — one bad task must not end the sweep
            counts["failed"] += 1
            logger.warning("draft PR not opened for task %s", task_id, exc_info=True)
            continue
        counts["opened" if opened else "skipped"] += 1
    return counts


async def retarget_completed_dependencies(session_factory: async_sessionmaker) -> None:
    """Retry child PR retargeting without rolling back a parent's completed merge."""
    from sqlalchemy.orm import aliased

    from app.domain.review.repositories import AcceptCardRepository
    from app.domain.room_task.models import Task, TaskStatus
    from app.domain.room_task.services import TaskService

    parent = aliased(Task)
    async with session_factory() as session:
        wanted = list(
            await session.scalars(
                select(Task.id)
                .join(parent, Task.base_task_id == parent.id)
                .where(
                    Task.status == TaskStatus.open,
                    parent.status == TaskStatus.closed,
                    (
                        parent.accepted_at.is_not(None)
                        | parent.delivered_head.is_not(None)
                    ),
                    Task.base_branch == parent.branch_name,
                )
            )
        )
    for task_id in wanted:
        try:
            async with session_factory() as session:
                task = await session.get(Task, task_id, with_for_update=True)
                if (
                    task is None
                    or task.status != TaskStatus.open
                    or task.base_task_id is None
                ):
                    continue
                ancestor = await TaskService(session).get(task.base_task_id)
                if ancestor is None or (
                    ancestor.accepted_at is None and ancestor.delivered_head is None
                ):
                    continue
                TaskService._bind_workspace(ancestor)
                base = ancestor.base_branch
                if base is None:
                    raise ValueError("Parent task has no target branch")
                if task.pr_number is not None:
                    from app.domain.review.services import AcceptService
                    from app.domain.topic.models import Topic

                    room = await session.get(Topic, task.room_id)
                    if room is None:
                        raise ValueError("Task room is missing")
                    client = await AcceptService(session)._app_pr_client(room)
                    if client is None:
                        raise RuntimeError(
                            "Cannot retarget the task PR without its GitHub connection"
                        )
                    await client.update_pr(task.pr_number, base=base)
                task.base_branch = base
                TaskService._bind_workspace(task)
                cards = AcceptCardRepository(session)
                for card in await cards.list_for_task(task.id):
                    if card.status in ("pending", "conflict"):
                        await cards.clear_approvals(card.id)
                        card.auto_merge_armed_by = None
                        card.auto_merge_armed_at = None
                        card.note = (
                            f"父任务已采纳，目标分支改为 {base}；"
                            "请集成目标分支并重新验证。"
                        )
                await session.commit()
        except Exception:
            logger.warning(
                "Task %s target branch update failed; retry next sweep",
                task_id,
                exc_info=True,
            )


async def _draft_pr_for_one_task(session: AsyncSession, task_id: uuid.UUID) -> bool:
    """Open and record this task's draft PR. False = there was nothing to do.

    **A task anybody has filed a card on is not this sweep's business**, and
    that — not the row lock — is what keeps it off a merged branch. The task's
    `status` cannot answer "has this been merged on GitHub", because the merge
    happens FIRST and the row is updated after
    (`AcceptService._merge_pr_for_accept`: merge API, then
    `_mark_task_merged`). A sweep holding the row lock in between sees
    `open` while the branch is, on GitHub, already squashed into main — and
    opens a PR nobody can ever close by merging it. Locking the row makes that
    window deterministic instead of removing it. The card does remove it: a
    filed card is the delivery, from that moment the PR belongs to it
    (`open_pr_for_card`), and no task is ever merged without one. So the
    question this asks is the question that has a stable answer.

    Every merging path goes through a card — the ordinary accept, an
    auto-merged armed card, a manual override, and the PR somebody merged on
    GitHub that the platform then settles — so all of them are excluded by the
    same one check.

    The row is still locked and re-read, for the narrower job it can actually
    do: two overlapping passes over one task must not both open a PR. The second
    sees `pr_number` set. Even if it somehow did not, `open_pr` adopts the PR
    already open on that head rather than creating a second — that is the belt,
    this is the braces.
    """
    from app.domain.review.repositories import AcceptCardRepository
    from app.domain.room_task.services import TaskService

    tasks = TaskService(session)
    task = await tasks.claim_for_pr(task_id)
    if task is None:
        return False
    cards = AcceptCardRepository(session)
    if await cards.list_for_task(task.id):
        return False
    pr = await _open_draft_for_task(session, task)
    if pr is None:
        return False
    await tasks.record_pr(
        task, number=int(pr["number"]), url=str(pr.get("html_url") or "")
    )
    return True


async def _open_draft_for_task(session: AsyncSession, task) -> dict | None:  # noqa: ANN001
    """The draft PR for one task, or None when this task cannot have one yet.

    None (not an error) for: a project with no App installation, a non-GitHub
    upstream, and — the ordinary case, on every tick — a branch with nothing on
    it. A newly created task has no changes to review until its first commit.
    """
    from app.domain.review import pr_text
    from app.domain.room_task.place import PlaceResolver
    from app.domain.workspace import identity

    project_id = task.project_id
    tokens = await github_app_tokens_for_project(project_id, session)
    if tokens is None:
        return None
    upstream = await asyncio.to_thread(ws.get_upstream, project_id)
    parsed = parse_github_repo(upstream)
    if parsed is None:
        return None
    branch = task.branch_name
    if not await asyncio.to_thread(
        ws.branch_has_commits, project_id, branch, base=task.base_branch
    ):
        return None
    place = await PlaceResolver(session).resolve(task.room_id)
    if place is None:
        return None
    room = place.room

    token, _ = await tokens.write_token()
    await asyncio.to_thread(ws.push_branch, project_id, branch, token)
    base = task.base_branch
    who = await identity.attribution(session, room)
    client = GitHubPRClient(*parsed, tokens)
    pr = await client.open_pr(
        head=branch,
        base=base,
        # No `Reviewed-by` and no card: nobody has accepted, and the subject
        # this change will land under is not written until somebody files a
        # card. `WIP:` says both — and 递卡 replaces it (`sync_pr_text`).
        title=f"WIP: {task.title or branch}"[:255],
        body=pr_text.pr_body(room, "", None, who),
        as_user_token=await _requester_token(session, room.id),
        draft=True,
    )
    logger.info(
        "draft PR #%s opened for task %s (%s)",
        pr.get("number"),
        task.id,
        pr.get("html_url"),
    )
    return pr


async def _requester_token(session: AsyncSession, topic_id: uuid.UUID) -> str | None:
    """The GitHub credential of the human this topic belongs to, so the PR is
    opened in their name. None whenever they have not connected GitHub, their
    token cannot be refreshed, or anything at all goes wrong — this is an
    attribution nicety and must never be the reason a PR fails to open.

    Who that human is comes from `identity.requester_handle`, not from
    `Topic.created_by`: on a 分身-split room the creator is the 分身's own
    `cheese-<hex12>` handle, which matches no account, so this returned None and
    every such PR opened as `cheesex-app[bot]`."""
    from app.domain.oauth.services import get_github_user_token_for_handle
    from app.domain.room_task.place import PlaceResolver
    from app.domain.workspace import identity

    try:
        place = await PlaceResolver(session).resolve(topic_id)
        if place is None:
            return None
        handle = await identity.requester_handle(session, place.room)
        if not handle:
            return None
        return await get_github_user_token_for_handle(session, handle)
    except Exception:  # noqa: BLE001
        logger.info("no requester token for topic %s", topic_id, exc_info=True)
        return None


async def _pr_text(
    session: AsyncSession,
    *,
    card_id: uuid.UUID,
    topic_id: uuid.UUID,
    branch: str,
) -> tuple[str, str]:
    """PR title/body from the card's change summary (`cheese accept-request
    --subject/--body`), falling back to the topic title when the card was filed
    without one.

    Same builders the merge path uses (`review/pr_text.py`), on purpose: the
    PR a reviewer reads and the squash commit that lands on main must not be
    able to say two different things about the same change.

    The routing bookkeeping the old body carried — 验收人, 路由理由, "采纳这张
    验收卡即合并本 PR" — is gone. It described the platform's workflow to people
    who were already inside it, while the reviewer opening the PR on GitHub
    wanted to know what changed and why."""
    from app.domain.review import pr_text
    from app.domain.review.repositories import AcceptCardRepository
    from app.domain.room_task.place import PlaceResolver
    from app.domain.workspace import identity

    place = await PlaceResolver(session).resolve(topic_id)
    card = await AcceptCardRepository(session).get(card_id)
    if place is None:
        return branch, f"Cheese-Topic: {topic_id}"
    topic = place.room
    who = await identity.attribution(session, topic, card=card)
    # No approver yet — the PR opens when the card is FILED, and 采纳 is what
    # merges it. `Reviewed-by` is written onto the squash commit at merge time,
    # by whoever actually clicks.
    return pr_text.change_subject(card, topic), pr_text.pr_body(topic, "", card, who)


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
    from app.domain.review.repositories import AcceptCardRepository
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
            if task is not None and task.pr_number is None:
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
    from app.domain.review.repositories import AcceptCardRepository

    note = (
        f"{PR_OPEN_FAILED_PREFIX}（{str(exc)[:300]}）。这张卡目前没有 PR；"
        "点采纳会现场重开 PR，开不出来采纳会停下，不会静默直推上游。"
    )[:2000]
    try:
        async with session_factory() as session:
            card = await AcceptCardRepository(session).get(card_id)
            if card is None:
                return
            notes.record(card, notes.NoteCode.pr_open_failed, note)
            await session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("PR publish failure not recorded on card %s", card_id)
