"""The acceptance main line: accept and its GitHub/discussion legs, PR
resolution, the merge verdict, settling, stopping, and publishing the PR."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, NoReturn

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.errors import ForbiddenError, ValidationError
from app.core.sentences import listing, say
from app.domain.review import (
    archive,
    merge_state,
    notes,
    pr_publish,
)
from app.domain.review import forge as forge_mod
from app.domain.review.merge_state import MergeVerdict, Who, whose_move
from app.domain.review.models import (
    AcceptCard,
    AcceptStatus,
)
from app.domain.room_task.services import TaskService
from app.domain.topic.models import Topic, TopicStatus

if TYPE_CHECKING:  # `github_pr` stays a lazy import at every call site
    from app.domain.project.protection import BranchProtection
    from app.domain.review.github_pr import PullRequestStatus

from urllib.parse import urlsplit

from app.domain.project.protection import branch_protection_of
from app.domain.review import services as pkg
from app.domain.review.services._shared import (
    _ACCEPT_NO_BRANCH_PREFIX,
    _ACCEPT_PR_OPEN_FAILED_PREFIX,
    _ACCEPT_PR_STALLED_PREFIX,
    EVENT_ACCEPT_DISMISSED,
    EVENT_ACCEPT_DONE,
    EVENT_ACCEPT_STOPPED,
    EVENT_PR_CLOSED,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARN,
    VOIDED_PREFIX,
    WHO_HUMAN,
    WHO_PLATFORM,
    _GitHubCredentials,
    approvals_required_of,
    logger,
    notice,
)


async def accept(
    self: pkg.AcceptService,
    *,
    card_id: uuid.UUID,
    decided_by: str,
    head_sha: str | None = None,
) -> AcceptCard:
    card = await self._card_or_404(card_id)
    # 机器闸门 (eval C2): the card isn't in the reviewer's hands yet / died.
    if card.status == AcceptStatus.pending_gate:
        raise ValidationError(say("checksStillRunning"))
    if card.status == AcceptStatus.gate_failed:
        raise ValidationError(say("checksFailedResubmit"))
    if card.status == AcceptStatus.gate_blocked:
        raise ValidationError(say("checksDidNotRun"))
    # pending → first attempt; conflict → retry after 芝士 resolved.
    if card.status not in (AcceptStatus.pending, AcceptStatus.conflict):
        raise ValidationError(say("reviewEndedNoAccept"))
    # 递给某个具体的人 (spec §4.4): only the routed reviewer may accept —
    # decided_by is the caller's verified actor handle, never body-trusted.
    if decided_by != card.reviewer_handle:
        raise ForbiddenError(say("acceptReviewerOnly"))
    topic = await self._topic_or_404(card.topic_id)
    # 合的是人看到的那个 commit：屏幕上那一版还在，才谈得上采纳它。
    seen_head = await self._seen_head_or_refresh(
        card, topic, head_sha, say("verbAccept")
    )

    # 归档会连带终结这个话题上还没决议的卡 (review/archive.py)，所以这里通常
    # 走不到；留着是为了兜住"归档与采纳同时发生"的竞态。重复采纳本身由上面的
    # 卡状态闸门挡（一张卡只能 accepted 一次），不再依赖话题被归档。
    if topic.status == TopicStatus.archived:
        raise ValidationError(say("topicArchivedNoAccept"))
    project = await self._projects.get(topic.project_id)
    self._forbid_ai(project, decided_by, say("reviewActionAccept"))

    # Institution protocol from linked Task Templates (spec §4.2).
    await self._enforce_protocol(topic, decided_by)

    # 主分支保护 (spec §4.4): the accept itself counts as the accepter's
    # vote (default requirement of 1 ⇒ 现行为不变); short of votes the whole
    # transaction rolls back and nothing merges.
    approvers = await self._repo.list_approver_handles(card_id)
    # Count this decision as a vote without persisting it yet. A merge can
    # fail outside SQLAlchemy; delaying the write keeps even callers that
    # catch ValidationError from accidentally committing a failed accept.
    votes = len(set(approvers) | {decided_by})
    required = approvals_required_of(project)
    if votes < required:
        raise ValidationError(
            say("approvalsShort", left=required - votes, n=votes, of=required)
        )

    forge = await self._resolve_forge(topic.project_id, card=card)
    return await forge.accept(self, card, topic, decided_by, seen_head=seen_head)


async def _accept_github(
    self: pkg.AcceptService,
    card: AcceptCard,
    topic: Topic,
    decided_by: str,
    *,
    seen_head: str | None,
) -> AcceptCard:
    """GitHub proposals never fall back to a local merge after a failure."""
    if card.pr_number is None:
        await self._publish_pr_for_accept(card, topic)
        if card.pr_number is not None:
            # PR 是这一秒才开出来的：卡面在此之前没有、现在也还没有一个
            # 被展示过的 head。「人看的是同一条分支」不等于「同一个
            # commit」—— 浏览器从来没有声明过它渲染的 diff 是哪个 sha，
            # 而分身边干边推是常态。所以这次不合，PR 留着（开 PR 是有价
            # 值的副作用，下次采纳就有 head 可比），head 镜像上卡，人重
            # 新看过再点。
            await self._refresh_never_shown_card(card, topic, say("verbAccept"))
    if card.pr_number is not None:
        assert seen_head is not None  # the guard above rules None out
        return await self._merge_pr_for_accept(
            card, topic, decided_by, seen_head=seen_head
        )
    work = await TaskService(self._session).get(card.task_id) if card.task_id else None
    if (card.change_subject or "").strip() or (work and work.branch_name):
        await self._stop_accept_no_branch(card, topic)
    # A legacy discussion without a branch has no change to merge.
    return await self._accept_discussion(card, topic, decided_by)


async def _accept_discussion(
    self: pkg.AcceptService, card: AcceptCard, topic: Topic, decided_by: str
) -> AcceptCard:
    """Record acceptance of a discussion with no code delivery."""
    await self._repo.add_approval(card.id, decided_by)
    now = datetime.now(UTC)
    card.status = AcceptStatus.accepted
    card.decided_by = decided_by
    card.decided_at = now
    notes.clear(card)
    await self._stamp_delivery(card, topic, by=decided_by, at=now)
    await self._mark_task_merged(card)
    await self._session.flush()
    await self._session.refresh(card)
    return card


async def _pr_repo_of(
    self: pkg.AcceptService, card: AcceptCard, topic: Topic
) -> tuple[str, str]:
    """(owner, repo) the card's PR lives in — from the card when recorded,
    else resolved from the project's upstream and backfilled onto the card
    (pr_publish records only pr_number/pr_url at filing time)."""

    # deferred-import: tests replace this name on app.domain.project.forge
    from app.domain.project.forge import binding_for_project, renamed_from

    binding = await binding_for_project(topic.project_id, self._session)
    if binding is None:
        raise ValidationError(say("reviewNoRepository"))
    if card.pr_repo and card.pr_repo != binding.repo:
        # The same repository under a new name (renamed on GitHub) keeps its
        # card; a different repository does not.
        if binding.kind != "github_app" or not await renamed_from(
            topic.project_id, card.pr_repo, self._session
        ):
            raise ValidationError(say("reviewRepositoryChanged"))
        if card.pr_url:
            card.pr_url = card.pr_url.replace(
                f"/{card.pr_repo}/", f"/{binding.repo}/", 1
            )
    if card.pr_url and urlsplit(card.pr_url).netloc != urlsplit(binding.url).netloc:
        raise ValidationError(say("reviewForgeMismatch"))
    card.pr_repo = binding.repo
    owner, repo = binding.repo.split("/", 1)
    return owner, repo


async def _dependency_block_reason(
    self: pkg.AcceptService, card: AcceptCard, status: PullRequestStatus
) -> str | None:
    if card.task_id is None:
        return None
    task = await TaskService(self._session).get(card.task_id)
    if task is None or task.base_task_id is None:
        return None
    parent = await TaskService(self._session).get(task.base_task_id)
    if parent is None or not status.base_ref:
        return say("dependencyUnconfirmed")
    if status.base_ref == parent.branch_name:
        return say("dependencyAwaitsParent", title=parent.title)
    if status.base_ref != task.base_branch:
        return say("dependencyBaseChanged")
    return None


async def _sync_dependency_target(
    self: pkg.AcceptService,
    card: AcceptCard,
    status: PullRequestStatus,
) -> bool:
    """Record a native PR retarget and invalidate reviews of its previous diff."""
    if card.task_id is None or not status.base_ref:
        return False
    task = await TaskService(self._session).get(card.task_id)
    return await self._sync_task_dependency_target(task, status)


async def _sync_task_dependency_target(
    self: pkg.AcceptService,
    task,
    status: PullRequestStatus,
    *,
    drop_dependency: bool = False,
) -> bool:
    # deferred-import: tests replace this name on app.domain.project.forge
    from app.domain.project.forge import default_branch

    if (
        task is None
        or task.base_task_id is None
        or (task.base_branch == status.base_ref and not drop_dependency)
    ):
        return False
    if status.base_ref != await default_branch(task.project_id, self._session):
        return False
    task.base_branch = status.base_ref
    # The agent explicitly chose a different target on the forge. A later
    # parent-close sweep must not overwrite that decision.
    task.base_task_id = None
    for related in await self._repo.list_for_task(task.id):
        if related.status in (AcceptStatus.pending, AcceptStatus.conflict):
            await self._repo.clear_approvals(related.id)
            related.auto_merge_armed_by = None
            related.auto_merge_armed_at = None
            related.pr_head_sha = None
            related.merge_state = None
    await self._session.flush()
    return True


async def _pr_verdict(
    self: pkg.AcceptService,
    *,
    card: AcceptCard,
    topic: Topic,
    owner: str,
    repo: str,
    creds: _GitHubCredentials,
    client,
    status: PullRequestStatus,
    ref: str,
) -> tuple[MergeVerdict, Who, BranchProtection, bool, list[merge_state.CheckRun]]:
    """The one merge-state computation (#718): gather this PR's raw
    signals and hand them to `merge_state.compute_merge_state`. Click-time
    and poll-time both come through here — the judgment exists once.

    Returns (verdict, whose move it is, the project's protection policy,
    whether GitHub itself is enforcing protection, the raw check runs —
    the poller's event table reads facts the verdict may have folded away:
    a conflicted PR is `dirty` no matter what its checks say, but a red
    check on it is still 芝士's to fix and must still reach it)."""
    # deferred-import: tests replace this name on app.domain.project.forge
    from app.domain.project.forge import default_branch

    project = await self._projects.get(topic.project_id)
    protection = branch_protection_of(project)
    # deferred-import: tests replace this name on app.domain.project.forge
    from app.domain.project.forge import binding_for_project

    binding = await binding_for_project(topic.project_id, self._session)
    enforces = (
        await pkg._github_enforces(f"{owner}/{repo}", creds.read)
        if binding is not None and binding.kind == "github_app"
        else False
    )

    raw_runs = await client.list_check_runs(
        owner=owner, repo=repo, ref=ref, token=creds.read
    )
    runs = [
        merge_state.CheckRun(
            name=str(r.get("name") or ""),
            status=str(r.get("status") or ""),
            conclusion=(
                str(r["conclusion"]) if r.get("conclusion") is not None else None
            ),
        )
        for r in raw_runs
    ]
    required = tuple(
        merge_state.RequiredCheck(name=rc.name, paths=rc.paths)
        for rc in protection.required_checks
    )

    # The two compare reads cost API calls, so each happens only when a
    # rule actually consumes it: ancestry for strict, the file list for a
    # path-scoped required check. Failures degrade to None — the verdict's
    # documented conservative fallbacks take over (scope unknown = the
    # check stays required; ancestry unknown = strict does not block).
    work = await TaskService(self._session).get(card.task_id) if card.task_id else None
    base = (work.base_branch if work else None) or await default_branch(
        topic.project_id, self._session
    )
    ancestry: str | None = None
    if protection.strict:
        try:
            ancestry = await client.compare_status(
                owner=owner, repo=repo, base=base, head=ref, token=creds.read
            )
        except Exception:  # noqa: BLE001 — ancestry unreadable ≠ blocked
            logger.warning("card %s: compare_status failed", card.id, exc_info=True)
    changed_paths: list[str] | None = None
    if any(rc.paths for rc in required):
        try:
            files = await client.compare_files(
                owner=owner, repo=repo, base=base, head=ref, token=creds.read
            )
        except Exception:  # noqa: BLE001 — scope unknown handled conservatively
            logger.warning("card %s: compare_files failed", card.id, exc_info=True)
            files = None
        changed_paths = None if files is None else [path for _, path in files]

    verdict = merge_state.compute_merge_state(
        github_mergeable_state=status.mergeable_state,
        github_mergeable=status.mergeable,
        check_runs=runs,
        changed_paths=changed_paths,
        required_checks=required,
        strict=protection.strict,
        base_ancestry=ancestry,
        github_enforces=enforces,
        draft=status.draft,
    )
    dependency = await self._dependency_block_reason(card, status)
    if dependency:
        verdict = MergeVerdict(
            state="blocked",
            reasons=(merge_state.MergeReason(kind="dependency", detail=dependency),),
        )
        return verdict, "agent", protection, False, runs
    return verdict, whose_move(verdict), protection, enforces, runs


async def _mirror_pr_verdict(
    self: pkg.AcceptService,
    card: AcceptCard,
    *,
    topic: Topic,
    owner: str,
    repo: str,
    creds: _GitHubCredentials,
    client,
    status: PullRequestStatus,
    ref: str,
) -> tuple[MergeVerdict, Who, BranchProtection, bool, list[merge_state.CheckRun]]:
    """问一次那个唯一的判官，把它的答案写到卡上 —— 中间的每一步都不许有人
    自己再判一遍（`_pr_verdict` 的 docstring）。轮询器、点击、以及读卡时补陈旧
    快照，三条路都从这一个门口过。"""
    verdict, who, protection, enforces, runs = await self._pr_verdict(
        card=card,
        topic=topic,
        owner=owner,
        repo=repo,
        creds=creds,
        client=client,
        status=status,
        ref=ref,
    )
    self._write_merge_mirror(card, verdict, who, ref)
    return verdict, who, protection, enforces, runs


async def _dismiss_stale_accept(
    self: pkg.AcceptService, *, card: AcceptCard, topic: Topic
) -> None:
    """新提交作废已有的采纳 (#718)。

    GitHub 的「Dismiss stale pull request approvals when new commits are
    pushed」，这里默认开着 —— GitHub 默认关，因为它假设推代码的是可信的
    人；这里推代码的是拿着 App 写权限的芝士。清掉旧 head 挣到的一切
    （批准票、auto-merge 布防），且只在真有东西被作废时说话。

    通知投给验收人和**被作废的那几票的主人**：重新投一次只有投票的人能做，
    而这句话已经点了他们的名字。"""

    project = await self._projects.get(topic.project_id)
    if not branch_protection_of(project).dismiss_stale:
        return
    approvers = await self._repo.list_approver_handles(card.id)
    armed = card.auto_merge_armed_by
    if not approvers and not armed:
        return
    await self._repo.clear_approvals(card.id)
    card.auto_merge_armed_by = None
    card.auto_merge_armed_at = None
    voided = listing(sorted({*approvers, *((armed,) if armed else ())}))
    await self._tell_the_reviewer(
        card,
        topic,
        say("acceptDismissed", pr=card.pr_number),
        meta=notice(
            EVENT_ACCEPT_DISMISSED,
            severity=SEVERITY_WARN,
            who=WHO_HUMAN,
            detail=say("acceptDismissedDetail", voided=voided, url=card.pr_url or ""),
            detail_label=say("labelWhyVoided"),
        ),
        also=(*approvers, *((armed,) if armed else ())),
    )


async def _settle_external_merge(
    self: pkg.AcceptService,
    *,
    card: AcceptCard,
    topic: Topic,
    status: PullRequestStatus,
) -> None:
    """Someone merged the PR on GitHub themselves (人工放行, another bot, the
    merge queue). Book it exactly like our own merge — because it is the same
    fact, and since #206 that fact is the whole of what the platform waits
    for."""
    card.pr_merged_at = status.merged_at or datetime.now(UTC)
    await self._mark_task_merged(card, delivered_head=status.head_sha)
    if status.merge_commit_sha:
        # Nice to have, not required: nothing downstream looks a run up by
        # this sha any more, it is just the truest record of what landed.
        card.pr_head_sha = status.merge_commit_sha
    await self._finish_pr_accept(
        card=card,
        topic=topic,
        merged_externally=card.note_code != notes.NoteCode.waiting_merge_queue,
    )


async def _void_closed_pr_card(
    self: pkg.AcceptService, *, card: AcceptCard, topic: Topic
) -> None:
    """The PR was closed on GitHub WITHOUT merging: the review is over.

    A human closing the PR is them saying "not this". Merging behind their
    back, or falling back to a local merge, would be the opposite of what
    they asked for; so the platform voids the card, the same terminal state
    a manual void writes. Delivering again is an ordinary new submission,
    with a new card.
    """
    headline = (
        f"{VOIDED_PREFIX}：PR #{card.pr_number} 已在 GitHub 关闭且没有合并，"
        "平台自动作废了这次审阅。要继续交付，重新提交审阅。"
    )
    card.status = AcceptStatus.revoked
    notes.record(card, notes.NoteCode.voided, archive.prefix_note(card.note, headline))
    card.decided_at = card.decided_at or datetime.now(UTC)
    await self._session.flush()
    await self._tell_the_reviewer(
        card,
        topic,
        say("prClosed", pr=card.pr_number),
        meta=notice(
            EVENT_PR_CLOSED,
            severity=SEVERITY_WARN,
            who=WHO_HUMAN,
            detail=say("prClosedDetail", url=card.pr_url or ""),
            detail_label=say("labelNextStep"),
        ),
    )


async def _finish_pr_accept(
    self: pkg.AcceptService,
    *,
    card: AcceptCard,
    topic: Topic,
    merged_externally: bool = False,
    headline: str = "",
) -> None:
    """The PR is merged, so the work is accepted (#206).

    This used to also require the deploy workflow the merge triggered to
    reach success. That gate is gone: merged is a fact about git that holds
    for every project, while "deployed" is a per-project ops concept the
    platform was in no position to define — and cards waited on deploy runs
    that were sometimes never created at all (three real merges on main,
    2026-08-11, produced zero runs), which is a deadlock, not a safeguard.
    Watching the deploy is real work and it keeps a home: the webhook
    primitive already exists for a pipeline to post its outcome into the
    topic, and #190's ops room is where that judgment belongs.

    `headline` is prefixed onto the card's note when the merge was NOT the
    ordinary all-green one — today that means 人工放行 (`FORCE_MERGED_
    PREFIX`), whose whole point is that the card afterwards says who
    decided to merge red and why. It must survive this method, which
    otherwise rewrites `note` wholesale.
    """
    now = datetime.now(UTC)
    card.status = AcceptStatus.accepted
    by = card.decided_by
    how = say("prMergedExternally") if merged_externally else say("prMerged")
    settled = f"PR #{card.pr_number} {how}：{card.pr_url}"
    notes.record(card, None, f"{headline}；{settled}" if headline else settled)
    # 交付完成 ≠ 话题结束 (#442 decision 1)：话题保持 active，归档由人来做。
    await self._stamp_delivery(card, topic, by=by, at=now)
    await self._session.flush()
    await self._session.refresh(card)
    accepted_line = (
        say("acceptDone", actor=by, pr=card.pr_number, how=how)
        if by
        else say("acceptDoneNoActor", pr=card.pr_number, how=how)
    )
    self._notify_merge_result(
        topic,
        accepted_line,
        task_id=card.task_id,
        meta=notice(
            EVENT_ACCEPT_DONE,
            severity=SEVERITY_INFO,
            who=WHO_PLATFORM,
            detail=say("acceptDoneDetail", url=card.pr_url),
            detail_label=say("labelDeliveryNote"),
        ),
    )


async def _resolve_forge(
    self: pkg.AcceptService, project_id: uuid.UUID, *, card: AcceptCard | None = None
) -> forge_mod.Forge:
    """Which forge this project's accept goes through — the one place the
    lane is decided (see app.domain.review.forge)."""
    return await forge_mod.resolve(
        project_id=project_id,
        session=self._session,
        proposal_url=card.pr_url if card is not None else None,
    )


async def _stop_accept_pr_unavailable(
    self: pkg.AcceptService, card: AcceptCard, topic: Topic, reason: str
) -> NoReturn:
    """绑定 GitHub 的项目采纳永不落 local merge (#363): when the card's PR
    cannot be merged right now (GitHub unreachable, PR closed unmerged, …)
    the accept STOPS — visibly and retryably — instead of bypassing the PR
    and its CI with a direct push. The note is persisted outside this
    transaction because the ValidationError below rolls it back.

    **Roll back BEFORE writing that note.** `_note_outside_accept_txn` uses
    its own connection, and by the time we get here this request's
    transaction may already hold a row lock on the very card it wants to
    write (`_publish_pr_for_accept` opens the PR and flushes `pr_number`
    onto the card, and SQLAlchemy's autoflush can push that UPDATE out even
    without an explicit flush). Two connections, one row, and the one
    holding the lock is the one waiting for the other — the request hangs
    until something times it out, and "采纳按钮点下去没反应" is the worst
    possible presentation of a path whose entire job is to fail visibly.
    The rollback loses nothing: this method always raises, so the accept
    transaction was never going to commit, and the PR itself was already
    recorded durably by `pr_publish.record_pr` on its own connection.

    The room notification is built and dispatched first, while `topic` is
    still live — after a rollback its attributes are expired and reading
    them would go back to the database for no reason."""
    why = reason or say("acceptStoppedPrStalled", pr=card.pr_number)
    card_id = card.id
    note = (
        f"{_ACCEPT_PR_STALLED_PREFIX}（{why}）。绑定 GitHub 的项目采纳只通过"
        "合并 PR 完成，平台不会绕过 PR 直推上游；处理后重试采纳。"
    )
    self._notify_merge_result(
        topic,
        say("acceptStoppedPrUnavailable"),
        task_id=card.task_id,
        meta=notice(
            EVENT_ACCEPT_STOPPED,
            severity=SEVERITY_ERROR,
            who=WHO_HUMAN,
            detail=say("acceptStoppedPrUnavailableDetail", why=why),
            detail_label=say("labelReason"),
        ),
    )
    await self._session.rollback()
    await self._note_outside_accept_txn(card_id, notes.NoteCode.accept_pr_stalled, note)
    raise ValidationError(say("acceptPrNotMerged", why=why))


async def _stop_accept_no_branch(
    self: pkg.AcceptService, card: AcceptCard, topic: Topic
) -> NoReturn:
    """带交付主张的卡开不出 PR，因为这棵树的分支上没有任何提交（2026-09-07
    卡 40be3e1a：改动被推到了别的分支）。旧路径把 `open_pr_for_card` 的 None
    当「纯讨论话题」落进本地合并 no-op——卡标成 accepted，人以为交付完成，而
    改动没有合进任何地方。主张交付却无从交付：停下，把该推哪条分支写在卡上，
    推上后重试采纳。

    Same rollback-first dance as `_stop_accept_pr_unavailable`, same reason:
    the note is written on its own connection and must never queue behind a
    row lock this doomed transaction still holds. Everything the note and
    notification need is read while the instances are live, before the
    rollback expires them."""
    card_id = card.id
    subject = (card.change_subject or "").strip()
    if card.task_id is None:
        raise ValidationError(say("legacyCardNoTask"))
    task_id = card.task_id
    work = await TaskService(self._session).require_in_room(topic.id, task_id)
    branch = work.branch_name
    note = (
        f"{_ACCEPT_NO_BRANCH_PREFIX}（{branch}），无法为「{subject}」开 PR。"
        f"改动可能提交到了其他分支，推送到 {branch} 后重新采纳。"
    )
    self._notify_merge_result(
        topic,
        say("acceptStoppedNoCommits"),
        task_id=card.task_id,
        meta=notice(
            EVENT_ACCEPT_STOPPED,
            severity=SEVERITY_ERROR,
            who=WHO_HUMAN,
            detail=say("acceptStoppedNoCommitsDetail", subject=subject, branch=branch),
            detail_label=say("labelReason"),
        ),
    )
    await self._session.rollback()
    await self._note_outside_accept_txn(card_id, notes.NoteCode.accept_no_branch, note)
    raise ValidationError(say("acceptBranchNoCommits", branch=branch))


async def _publish_pr_for_accept(
    self: pkg.AcceptService, card: AcceptCard, topic: Topic
) -> None:
    """无 PR 卡在采纳现场补开 App PR（#296 stage 1 的生产回归修复）.

    A fire-and-forget publish can fail or still be in flight when the
    human clicks — dropping such a card into the local-merge branch
    direct-pushed merge commits to main with no PR at all (dev: c33cfabf,
    8f9b9d94). The repair is to open the App PR HERE and let the click
    merge exactly that PR.

    Synchronous by design: the accept's outcome must depend on the publish
    result, and the click already runs the merge API call inside the
    accept request — one more push plus one create-PR call is the same
    latency class. Racing a still-in-flight fire-and-forget publish is
    benign: the push is force-with-lease of the same branch, `open_pr`
    adopts an already-open PR for the head instead of failing, and
    `record_pr` writes the same numbers this method records.

    On success the PR is recorded on the card DURABLY, outside the accept
    transaction (`pr_publish.record_pr`): if the accept goes on to fail —
    a refusal from the merge call raises ValidationError and rolls this
    request back — the card must keep the PR it now rides, or the next
    attempt would look PR-less again. `open_pr_for_card` can still return
    None (its own not-applicable checks); with the caller pre-checking
    `hosts_proposals`, in practice that means a topic with no tree branch.
    The card is left untouched, and the CALLER decides what a branchless
    topic means: a legacy card with no delivery claim proceeds into the
    no-op local merge, while a card claiming a change stops the accept
    (`_stop_accept_no_branch` — 2026-09-07 卡 40be3e1a).
    When opening the PR FAILS, the accept STOPS: the reason is persisted
    on the card outside this transaction, the room is told, and
    ValidationError surfaces to the caller. Silently direct-pushing main
    without a PR is never a fallback on a bound project (#363, all
    commits go through PR)."""
    try:
        pr = await pr_publish.open_pr_for_card(
            self._session,
            card_id=card.id,
            topic_id=topic.id,
            project_id=topic.project_id,
        )
    except Exception as exc:  # noqa: BLE001 — surface on the card; never direct-push
        # Read every attribute we still need BEFORE the rollback below:
        # rollback expires the instance, and an expired attribute reloads
        # itself with synchronous IO that an AsyncSession cannot perform
        # (MissingGreenlet) — which would replace this readable failure
        # with an unreadable one.
        card_id = card.id
        logger.exception("accept-time PR publication failed for card %s", card_id)
        reason = f"{exc}"[:300]
        note = (
            f"{_ACCEPT_PR_OPEN_FAILED_PREFIX}（{reason}）。"
            "平台不会在没有 PR 的情况下把改动直推上游；修复后重试采纳。"
        )
        self._notify_merge_result(
            topic,
            say("acceptStoppedPrOpenFailed"),
            task_id=card.task_id,
            meta=notice(
                EVENT_ACCEPT_STOPPED,
                severity=SEVERITY_ERROR,
                who=WHO_HUMAN,
                detail=say("acceptStoppedPrOpenFailedDetail", reason=reason),
                detail_label=say("labelReason"),
            ),
        )
        # Roll back first, for the same reason as `_stop_accept_pr_
        # unavailable`: the out-of-transaction note writes the card row on
        # its own connection, and it must never be able to queue behind a
        # lock this doomed transaction is still holding.
        await self._session.rollback()
        await self._note_outside_accept_txn(
            card_id, notes.NoteCode.accept_pr_open_failed, note
        )
        raise ValidationError(say("acceptPrOpenFailed")) from exc
    if pr is None:
        return  # PR 路对这个项目/话题不适用 — 本地合并就是它唯一的采纳方式
    # Durable first (survives a later rollback of this request), then the
    # in-memory mirror so the rest of THIS accept sees the PR. Same
    # bind-not-global-factory reasoning as _note_outside_accept_txn.
    factory = async_sessionmaker(self._session.bind, expire_on_commit=False)
    await pr_publish.record_pr(factory, card_id=card.id, pr=pr)
    card.pr_number = int(pr["number"])
    card.pr_url = str(pr.get("html_url") or "")[:255] or None
    if card.note_code is notes.NoteCode.pr_open_failed:
        notes.clear(card)  # mirror record_pr's stale-failure-note clearing
    await self._session.flush()
