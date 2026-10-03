"""Arming auto-merge, the merge mirror, the actual merge call and the
merge-queue bookkeeping."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.core.errors import ForbiddenError, ValidationError
from app.domain.block.notice_text import say
from app.domain.repository import identity
from app.domain.review import (
    merge_state,
    notes,
    pr_text,
)
from app.domain.review.merge_state import MergeVerdict, Who
from app.domain.review.models import (
    AcceptCard,
    AcceptStatus,
)
from app.domain.topic.models import Topic

if TYPE_CHECKING:  # `github_pr` stays a lazy import at every call site
    from app.domain.project.protection import BranchProtection

from app.domain.review import services as pkg
from app.domain.review.services._shared import (
    EVENT_MERGE_REFUSED,
    SEVERITY_ERROR,
    WHO_HUMAN,
    _capped,
    _GitHubCredentials,
    logger,
    notice,
)


async def arm_auto_merge(
    self: pkg.AcceptService,
    *,
    card_id: uuid.UUID,
    decided_by: str,
    enabled: bool,
    head_sha: str | None = None,
) -> AcceptCard:
    """绿了自动合 (#718)，GitHub auto-merge 的对应物。

    布防不是决议：卡留在 pending，规则满足时轮询器以布防人的名义合并（布防
    人的那票算进批准数）。新提交作废采纳（dismiss_stale）同样解除布防 ——
    机器合的永远是布防人看过的那份，或者不合。

    谁能布防：这张卡的验收人（跟采纳同一个人 —— 布防就是「提前采纳」）。
    项目要先开 `auto_merge_allowed`。解除给同一个人加布防人自己。

    布防等于提前采纳，所以它跟采纳一样要声明「我看的是哪一版」
    （`_seen_head_or_refresh`）：屏幕上那版已经过时、或者卡面还没显示过任何
    版本的话，布防就是替一段没人看过的代码预先按下同意。
    **这跟合并态是不是 blocked 无关**——这个开关本来就只在
    BLOCKED / BEHIND 出现，规则没满足正是布防的前提，拒的理由只有「旧 SHA」
    一个。解除布防不需要看过任何版本：撤销自己的同意什么都不会合并。
    """
    from app.domain.project.protection import branch_protection_of

    card = await self._card_or_404(card_id)
    if card.status != AcceptStatus.pending or card.pr_number is None:
        raise ValidationError(say("autoMergeNeedsOpenPr"))
    topic = await self._topic_or_404(card.topic_id)
    project = await self._projects.get(topic.project_id)
    self._forbid_ai(project, decided_by, say("reviewActionAutoMerge"))
    if enabled and not branch_protection_of(project).auto_merge_allowed:
        raise ValidationError(say("autoMergeNotEnabled"))
    allowed = {card.reviewer_handle}
    if card.auto_merge_armed_by:
        allowed.add(card.auto_merge_armed_by)
    if decided_by not in allowed:
        raise ForbiddenError(say("autoMergeReviewerOnly"))
    if enabled:
        await self._seen_head_or_refresh(card, topic, head_sha, say("verbArmAutoMerge"))
        card.auto_merge_armed_by = decided_by
        card.auto_merge_armed_at = datetime.now(UTC)
    else:
        card.auto_merge_armed_by = None
        card.auto_merge_armed_at = None
    await self._session.flush()
    await self._session.refresh(card)
    return card


def _write_merge_mirror(
    self: pkg.AcceptService,
    card: AcceptCard,
    verdict: MergeVerdict,
    who: Who,
    head_sha: str,
) -> None:
    """Mirror the verdict onto the card — what the card UI shows (#718).

    `since` is when this (state, head) pair started holding, carried over
    from the previous mirror when unchanged: the required-check grace
    clock reads it."""
    now_iso = datetime.now(UTC).isoformat()
    prev = card.merge_state if isinstance(card.merge_state, dict) else {}
    since = (
        prev.get("since") or now_iso
        if prev.get("state") == verdict.state and prev.get("head_sha") == head_sha
        else now_iso
    )
    card.merge_state = {
        "state": verdict.state,
        "who": who,
        "reasons": [
            {"kind": r.kind, "checks": list(r.checks), "detail": r.detail}
            for r in verdict.reasons
        ],
        "head_sha": head_sha,
        "checked_at": now_iso,
        "since": since,
    }


async def _merge_pr_for_accept(
    self: pkg.AcceptService,
    card: AcceptCard,
    topic: Topic,
    decided_by: str,
    *,
    seen_head: str,
) -> AcceptCard:
    """App forge: 采纳 = 当场调合并 API，合的是人看到的那个 commit (#718).

    The gate keeping "只在绿的时候合" is the same merge-state computation
    the poller mirrors onto the card — evaluated fresh right here, because
    the mirror may be up to a poll interval stale. GitHub 自己开了保护的
    项目直接调 API（405 就是被拦住，平台一个字不重算）；其余项目 clean /
    unstable（红的不在必跑名单）才合，非绿拒绝采纳并把状态和原因写进响应。

    The merge call carries the head the human saw — `seen_head`, the sha
    the BROWSER rendered, already checked against the card by
    `_seen_head_or_refresh`, and never anything else: there is no "just
    read the live head" fallback, because a live head is by definition one
    no screen has shown. Any push that landed after their look — before the
    click (live head differs) or during it (GitHub answers 409) — refreshes
    the card instead of merging: head updated, approvals cleared when the
    project dismisses stale accepts, and the human asked to look again.
    #422's whole authorize-then-drift apparatus is replaced by this one API
    parameter plus dismiss-stale.
    """

    number = card.pr_number
    assert number is not None  # caller checked; keeps the type checker honest

    creds, why = await self._app_credentials(topic)
    if creds is None:
        await self._stop_accept_pr_unavailable(
            card, topic, say("acceptStoppedNoCredentials", why=why)
        )
    try:
        owner, repo = await self._pr_repo_of(card, topic)
    except ValidationError as exc:
        await self._stop_accept_pr_unavailable(card, topic, str(exc))

    client = await self._status_client(topic.project_id)
    try:
        status = await client.pull_request_status(
            owner=owner, repo=repo, number=number, token=creds.read
        )
    except Exception as exc:  # noqa: BLE001 — stop visibly; never local-merge
        logger.warning(
            "cannot read PR #%s for card %s at accept time: %s",
            number,
            card.id,
            exc,
        )
        await self._stop_accept_pr_unavailable(
            card,
            topic,
            _capped("acceptStoppedStatusUnread", pr=number, error=str(exc)),
        )

    if status.merged:
        # 有人已经在 GitHub 上合了这个 PR —— 同一件事，照单收下。
        card.pr_merged_at = status.merged_at or datetime.now(UTC)
        await self._mark_task_merged(card, delivered_head=status.head_sha)
        if status.merge_commit_sha:
            card.pr_head_sha = status.merge_commit_sha
        return await self._conclude_pr_accept(
            card, topic, decided_by, merged_externally=True
        )
    if status.state == "closed":
        await self._stop_accept_pr_unavailable(
            card, topic, say("acceptStoppedPrClosed", pr=number)
        )

    # 合的是人看到的那个 commit：浏览器渲染时卡面上的 head，一个字都不兜底。
    # GitHub 上的合并永远不用「现取的 head」——那种 commit 没有在任何界面上
    # 出现过（`_seen_head_or_refresh` 是这条规矩的入口闸）。
    seen = seen_head
    if status.head_sha != seen:
        await self._refresh_stale_card(card, topic, live_head=status.head_sha)
        raise ValidationError(say("prNewCommitsAfterView", number=number))

    try:
        verdict, who, protection, enforces, _runs = await self._pr_verdict(
            card=card,
            topic=topic,
            owner=owner,
            repo=repo,
            creds=creds,
            client=client,
            status=status,
            ref=seen,
        )
    except Exception as exc:  # noqa: BLE001 — stop visibly; never guess green
        logger.warning(
            "cannot compute merge state for PR #%s at accept time: %s",
            number,
            exc,
        )
        await self._stop_accept_pr_unavailable(
            card,
            topic,
            _capped("acceptStoppedMergeStateUnread", pr=number, error=str(exc)),
        )
    self._write_merge_mirror(card, verdict, who, seen)
    # GitHub enforcing → its merge API is the gate (405 = blocked, 如实转
    # 译). Platform enforcing → only clean, or unstable whose reds are not
    # on the required roster, may merge; everything else is refused with
    # the state and its reasons — the UI shouldn't have lit the button,
    # this is the defensive twin of that rule.
    if not enforces and verdict.state not in ("clean", "unstable"):
        detail = "；".join(r.detail for r in verdict.reasons if r.detail)
        raise ValidationError(
            say("acceptBlocked", state=verdict.state, detail=detail or "规则未满足")
        )

    attribution = await identity.attribution(
        self._session, topic, card=card, decided_by=decided_by
    )
    from app.domain.project.forge import ensure_author_email

    if attribution.author:
        await ensure_author_email(
            topic.project_id, self._session, attribution.author.email
        )
    try:
        result = await client.merge_pull_request(
            owner=owner,
            repo=repo,
            number=number,
            token=creds.write,
            commit_title=pr_text.merge_commit_title(card, topic, number),
            commit_message=pr_text.merge_commit_message(
                topic, decided_by, card, attribution
            ),
            sha=seen,
        )
    except Exception:  # noqa: BLE001 — the remote may have merged before disconnecting
        logger.exception("merge request failed for card %s", card.id)
        await self._stop_accept_pr_unavailable(
            card,
            topic,
            say("acceptStoppedMergeUnconfirmed", pr=number),
        )
    if result.stale_head:
        # 芝士在点击和合并之间又推了 —— GitHub 拦下了那个没人看过的 commit。
        live = ""
        try:
            live = await client.pull_request_head_sha(
                owner=owner, repo=repo, number=number, token=creds.read
            )
        except Exception:  # noqa: BLE001 — refresh with what we know
            logger.warning("card %s: post-409 head read failed", card.id)
        await self._refresh_stale_card(card, topic, live_head=live)
        raise ValidationError(say("prNewCommitsDuringAccept", number=number))
    if result.queued:
        await self._record_queue_entry(card, decided_by)
        return card
    if result.sha is None:
        # A faithful 405: GitHub (or its enforced protection) said no.
        reason = result.blocked_reason or say("reasonUnstated")
        self._notify_merge_result(
            topic,
            say("mergeRefusedOnAccept", pr=number),
            meta=notice(
                EVENT_MERGE_REFUSED,
                severity=SEVERITY_ERROR,
                who=WHO_HUMAN,
                detail=say("mergeRefusedDetail", reason=reason, url=card.pr_url or ""),
                detail_label=say("labelGithubReply"),
            ),
        )
        raise ValidationError(say("githubMergeRefused", pr=number, reason=reason))

    card.pr_merged_at = datetime.now(UTC)
    await self._mark_task_merged(card, delivered_head=seen)
    card.pr_head_sha = result.sha  # the merge commit, for the record
    return await self._conclude_pr_accept(card, topic, decided_by)


async def _record_queue_entry(
    self: pkg.AcceptService, card: AcceptCard, decided_by: str
) -> None:
    card.decided_by = decided_by
    card.decided_at = datetime.now(UTC)
    card.auto_merge_armed_by = None
    card.auto_merge_armed_at = None
    await self._repo.add_approval(card.id, decided_by)
    self._write_merge_mirror(
        card,
        MergeVerdict(
            state="blocked",
            reasons=(
                merge_state.MergeReason(kind="ci_running", detail="等待合并队列检查"),
            ),
        ),
        "ci",
        card.pr_head_sha or "",
    )
    notes.record(
        card,
        notes.NoteCode.waiting_merge_queue,
        f"PR #{card.pr_number} 已进入合并队列，等待队列检查和实际合并。",
    )
    await self._session.flush()
    await self._session.refresh(card)


async def _conclude_pr_accept(
    self: pkg.AcceptService,
    card: AcceptCard,
    topic: Topic,
    decided_by: str,
    *,
    merged_externally: bool = False,
) -> AcceptCard:
    """Shared tail of a click-time PR accept: record the decision and vote,
    then the common merged-PR bookkeeping (`_finish_pr_accept`)."""
    await self._repo.add_approval(card.id, decided_by)
    card.decided_by = decided_by
    card.decided_at = datetime.now(UTC)
    card.auto_merge_armed_by = None
    card.auto_merge_armed_at = None
    await self._finish_pr_accept(
        card=card, topic=topic, merged_externally=merged_externally
    )
    await self._session.refresh(card)
    return card


async def _merge_armed_card(
    self: pkg.AcceptService,
    *,
    card: AcceptCard,
    topic: Topic,
    owner: str,
    repo: str,
    creds: _GitHubCredentials,
    client,
    protection: BranchProtection,
    chat_service,
    runner,
) -> None:
    """绿了自动合 (#718)：the armed card's rules are satisfied — merge it
    with the armer's name on the decision, still guarded by the head sha
    (a push racing this merge gets a 409 and the next tick's
    dismiss-stale handles it, exactly like the click path)."""
    number = card.pr_number
    assert number is not None  # caller checked; keeps the type checker honest
    armer = card.auto_merge_armed_by or ""
    approvers = await self._repo.list_approver_handles(card.id)
    votes = len(set(approvers) | {armer})
    if votes < protection.approvals_required:
        await self._note_needs_human(
            card=card,
            topic=topic,
            reason=say(
                "mergeWithheldVotes",
                votes=votes,
                required=protection.approvals_required,
            ),
            explain=say("mergeWithheldVotesWhy"),
        )
        await self._session.flush()
        return
    attribution = await identity.attribution(
        self._session, topic, card=card, decided_by=armer
    )
    from app.domain.project.forge import ensure_author_email

    if attribution.author:
        await ensure_author_email(
            topic.project_id, self._session, attribution.author.email
        )
    result = await client.merge_pull_request(
        owner=owner,
        repo=repo,
        number=number,
        token=creds.write,
        commit_title=pr_text.merge_commit_title(card, topic, number),
        commit_message=pr_text.merge_commit_message(topic, armer, card, attribution),
        sha=card.pr_head_sha,
    )
    if result.stale_head:
        # head 在这一拍里又动了 —— 下一拍镜像到新 head，作废条款接手。
        await self._session.flush()
        return
    if result.queued:
        await self._record_queue_entry(card, armer)
        return
    if result.sha is None:
        await self._note_merge_blocked(
            card=card,
            topic=topic,
            reason=result.blocked_reason or "",
            chat_service=chat_service,
            runner=runner,
        )
        await self._session.flush()
        return
    card.pr_merged_at = datetime.now(UTC)
    await self._mark_task_merged(card, delivered_head=card.pr_head_sha)
    card.pr_head_sha = result.sha
    await self._repo.add_approval(card.id, armer)
    card.decided_by = armer
    card.decided_at = datetime.now(UTC)
    await self._finish_pr_accept(card=card, topic=topic)


async def merge_queued_pr(self: pkg.AcceptService, task_id: uuid.UUID) -> int | None:
    """The task's PR number while its card waits in the merge queue."""
    cards = await self._repo.list_live_for_places(
        [task_id], statuses=(AcceptStatus.pending,)
    )
    return next(
        (
            card.pr_number
            for card in cards
            if card.task_id == task_id
            and card.note_code == notes.NoteCode.waiting_merge_queue
        ),
        None,
    )


async def _cancel_queued_accept(self: pkg.AcceptService, card: AcceptCard) -> None:
    if card.note_code != notes.NoteCode.waiting_merge_queue:
        return
    assert card.pr_number is not None
    topic = await self._topic_or_404(card.topic_id)
    creds, why = await self._pr_poll_credentials(card, topic)
    if creds is None:
        raise ValidationError(say("mergeQueueLeaveFailed", why=why))
    owner, repo = await self._pr_repo_of(card, topic)
    client = await self._status_client(topic.project_id)
    dequeue = getattr(client, "dequeue_pull_request", None)
    if dequeue is None:
        raise ValidationError(say("mergeQueueLeaveUnsupported"))
    await dequeue(owner=owner, repo=repo, number=card.pr_number, token=creds.write)
    status = await client.pull_request_status(
        owner=owner, repo=repo, number=card.pr_number, token=creds.read
    )
    if status.merged:
        raise ValidationError(say("prMergedCantWithdraw"))
