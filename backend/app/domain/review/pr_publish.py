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
from app.domain.project.forge import branch_head, proposal_client, status_client
from app.domain.review import notes
from app.domain.review.forgejo_pr import ForgejoPRClient
from app.domain.review.github_pr import GitHubPRClient

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
    return bool(settings.forgejo_url) or (
        bool(settings.github_app_id) and bool(settings.github_app_private_key_path)
    )


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
    client = await proposal_client(project_id, session)
    if client is None:
        return None
    from app.domain.review.repositories import AcceptCardRepository
    from app.domain.room_task.services import TaskService

    card = await AcceptCardRepository(session).get(card_id)
    if card is None or card.task_id is None:
        return None
    task = await TaskService(session).require_in_room(topic_id, card.task_id)
    if not task.branch_name or not await branch_head(
        project_id, session, task.branch_name
    ):
        return None  # discussion-only topic — nothing a PR could carry

    branch = task.branch_name
    base = task.base_branch
    if base is None:
        raise ValueError("Task has no target branch")

    title, body = await _pr_text(
        session, card_id=card_id, topic_id=topic_id, branch=branch
    )
    opened = await client.open_pr(
        head=branch,
        base=base,
        title=title,
        body=body,
    )
    pr = opened.pr
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


async def sweep_draft_prs(
    session_factory: async_sessionmaker, project_id: uuid.UUID | None = None
) -> dict[str, int]:
    """Open draft PRs for task branches with commits and no existing PR.

    Commits arrive from different executors through Git; the shared PR poller
    observes them after synchronization. Delay is one poll interval plus the
    time spent on earlier tasks in this pass. Each task uses its own session
    so a failed write cannot poison the remaining deliveries.
    """
    from app.domain.room_task.services import TaskService

    counts = {"opened": 0, "skipped": 0, "failed": 0}
    await retarget_completed_dependencies(session_factory, project_id)
    if not enabled():
        return counts
    async with session_factory() as session:
        wanted = [
            t.id
            for t in await TaskService(session).open_without_pr()
            if project_id is None or t.project_id == project_id
        ]
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


async def retarget_completed_dependencies(
    session_factory: async_sessionmaker, project_id: uuid.UUID | None = None
) -> None:
    """Retarget delivered dependencies and persist instructions for the executor."""
    from sqlalchemy.orm import aliased

    from app.domain.agent.announce import announce
    from app.domain.agent.platform_notices import (
        EVENT_DEPENDENCY_CLOSED,
        SEVERITY_INFO,
        WHO_CHEESE,
        notice,
    )
    from app.domain.block.models import AGENT_NOTICE_META_KEY
    from app.domain.idempotency import store as idem
    from app.domain.idempotency.keys import action_key
    from app.domain.review.models import AcceptStatus
    from app.domain.review.repositories import AcceptCardRepository
    from app.domain.room_task.models import Task, TaskStatus
    from app.domain.room_task.services import TaskService

    parent = aliased(Task)
    query = (
        select(Task.id)
        .join(parent, Task.base_task_id == parent.id)
        .where(Task.status == TaskStatus.open, parent.status == TaskStatus.closed)
    )
    if project_id is not None:
        query = query.where(Task.project_id == project_id)
    async with session_factory() as session:
        wanted = list(await session.scalars(query))
    for task_id in wanted:
        try:
            # Decide on an unlocked read, call GitHub with no transaction open,
            # then lock the row only to record it. A row lock held across the
            # GitHub call would queue every other writer of this task behind
            # it with a pool connection each (dev outage of 2026-09-18).
            async with session_factory() as session:
                task = await session.get(Task, task_id)
                if (
                    task is None
                    or task.status != TaskStatus.open
                    or task.base_task_id is None
                ):
                    continue
                ancestor = await TaskService(session).get(task.base_task_id)
                if ancestor is None or ancestor.status != TaskStatus.closed:
                    continue
                delivered = bool(ancestor.accepted_at or ancestor.delivered_head)
                base = ancestor.base_branch
                if delivered and base is None:
                    raise ValueError("Parent task has no target branch")
                seen = (task.base_task_id, task.base_branch, task.pr_number)
                parent_state = (
                    ancestor.closed_at,
                    ancestor.accepted_at,
                    ancestor.delivered_head,
                )
                key = action_key(
                    task.id, "dependency_closed", ancestor.id, *parent_state
                )
                if await idem.stored_result(session, key) is not None:
                    continue
                retarget = delivered and task.base_branch == ancestor.branch_name
                client = None
                if retarget and task.pr_number is not None:
                    from app.domain.review.services import AcceptService
                    from app.domain.topic.models import Topic

                    room = await session.get(Topic, task.room_id)
                    if room is None:
                        raise ValueError("Task room is missing")
                    client = await AcceptService(session)._app_pr_client(room)
                    if client is None:
                        raise RuntimeError(
                            "Cannot retarget the task PR without its forge connection"
                        )
            if client is not None:
                # Setting the base is idempotent: a sweep that records nothing
                # below simply sets it again next time.
                await client.update_pr(seen[2], base=base)
            async with session_factory() as session:
                task = await session.get(Task, task_id, with_for_update=True)
                if (
                    task is None
                    or task.status != TaskStatus.open
                    or (task.base_task_id, task.base_branch, task.pr_number) != seen
                ):
                    continue
                ancestor = await session.get(Task, task.base_task_id)
                if (
                    ancestor is None
                    or ancestor.status != TaskStatus.closed
                    or (
                        ancestor.closed_at,
                        ancestor.accepted_at,
                        ancestor.delivered_head,
                    )
                    != parent_state
                ):
                    continue
                if not await idem.claim(
                    session, key, action="dependency_closed", scope_id=str(task.id)
                ):
                    continue
                if retarget:
                    task.base_branch = base
                outcome = "已合并" if delivered else "已关闭，未交付"
                headline = f"父任务{outcome}，子任务需要重新检查依赖"
                cards = AcceptCardRepository(session)
                parent_card = (await cards.latest_by_task([ancestor.id])).get(
                    ancestor.id
                )
                rejection = (
                    parent_card
                    if not delivered
                    and parent_card is not None
                    and parent_card.status == AcceptStatus.rejected
                    else None
                )
                instruction = (
                    f"任务 {task.id} 的父任务 {ancestor.id} {outcome}。\n"
                    f'先执行 cd "$(cheese worktree {task.id})"。\n'
                    + (
                        f"当前目标分支为 {task.base_branch}。获取远端分支，"
                        "将本任务的提交整理到目标分支上，解决冲突，重新运行检查并推送。"
                        "父任务可能采用 squash 合并，请核对补丁，"
                        "避免重复带入父任务的修改。"
                        if delivered
                        else "保留现有工作，检查本任务依赖了哪些尚未交付的修改。"
                        "根据任务要求决定移除依赖、独立实现或报告无法继续的原因；"
                        "不要把父任务关闭当作其代码已经合并，也不要自动关闭子任务。"
                    )
                    + "行动前重新读取任务状态；任务已关闭时不要继续修改。"
                )
                if rejection is not None:
                    instruction += f"\n父任务最近一张验收卡 {rejection.id} 被驳回。" + (
                        f"驳回理由原文：\n{rejection.note}"
                        if rejection.note
                        else "验收人没有填写驳回理由。"
                    )
                for card in await cards.list_for_task(task.id):
                    if card.status in ("pending", "conflict"):
                        await cards.clear_approvals(card.id)
                        card.auto_merge_armed_by = None
                        card.auto_merge_armed_at = None
                        card.note = headline
                block = await announce(
                    session,
                    place_id=task.room_id,
                    content=headline,
                    meta={
                        **notice(
                            EVENT_DEPENDENCY_CLOSED,
                            severity=SEVERITY_INFO,
                            who=WHO_CHEESE,
                            detail=instruction,
                            detail_label="下一步",
                        ),
                        AGENT_NOTICE_META_KEY: instruction,
                        "dependency_task_id": str(task.id),
                        "dependency_rejection": (
                            {
                                "card_id": str(rejection.id),
                                "decided_by": rejection.decided_by,
                                "reason": rejection.note,
                            }
                            if rejection is not None
                            else None
                        ),
                    },
                )
                if block is None:
                    raise ValueError("Task room is missing")
                await idem.record_result(session, key, {"block_id": str(block.id)})
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
    client = await proposal_client(project_id, session)
    if client is None:
        return None
    branch = task.branch_name
    head = await branch_head(project_id, session, branch)
    if head is None:
        return None
    token, _ = await client.tokens.installation_token()
    reader = await status_client(project_id, session)
    from app.domain.project.forge import binding_for_project

    binding = await binding_for_project(project_id, session)
    if binding is None:
        return None
    owner, repo = binding.repo.split("/", 1)
    difference = await reader.compare_status(
        owner=owner, repo=repo, base=task.base_branch, head=head, token=token
    )
    if difference not in ("ahead", "diverged"):
        return None
    place = await PlaceResolver(session).resolve(task.room_id)
    if place is None:
        return None
    room = place.room

    base = task.base_branch
    who = await identity.attribution(session, room, task_id=task.id)
    from app.domain.project.forge import ensure_author_email

    if who.author:
        await ensure_author_email(project_id, session, who.author.email)
    opened = await client.open_pr(
        head=branch,
        base=base,
        # No `Reviewed-by` and no card: nobody has accepted, and the subject
        # this change will land under is not written until somebody files a
        # card. `WIP:` says both — and 递卡 replaces it (`sync_pr_text`).
        title=f"WIP: {task.title or branch}"[:255],
        body=pr_text.pr_body(room, "", None, who),
        draft=True,
    )
    pr = opened.pr
    logger.info(
        "draft PR #%s opened for task %s (%s)",
        pr.get("number"),
        task.id,
        pr.get("html_url"),
    )
    return pr


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
    from app.domain.project.forge import ensure_author_email

    if who.author:
        await ensure_author_email(topic.project_id, session, who.author.email)
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
