"""Everything that ends a card other than accept: approve, reject,
revoke, void, mark-ready, redescribe, push-fix and force-merge."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.core.errors import ForbiddenError, ValidationError
from app.core.sentences import exception_text, say
from app.domain.block.about import EventAbout, landing
from app.domain.block.models import AuthorType, BlockKind
from app.domain.membership.services import MemberService
from app.domain.repository import identity
from app.domain.review import (
    archive,
    commit_message,
    notes,
    pr_publish,
    pr_text,
)
from app.domain.review.models import (
    AcceptCard,
    AcceptStatus,
)
from app.domain.room_task.models import TaskStatus
from app.domain.room_task.services import TaskService
from app.domain.topic.models import Topic, TopicStatus

if TYPE_CHECKING:  # `github_pr` stays a lazy import at every call site
    pass

from app.domain.review import services as pkg
from app.domain.review.services._shared import (
    EVENT_CARD_REDESCRIBED,
    EVENT_CARD_VOIDED,
    EVENT_FORCE_MERGED,
    FORCE_MERGED_PREFIX,
    SEVERITY_INFO,
    SEVERITY_WARN,
    VOIDED_PREFIX,
    WHO_CHEESE,
    WHO_HUMAN,
    BlockRepository,
    _force_merge_verdict,
    logger,
    notice,
)


async def approve(
    self: pkg.AcceptService, *, card_id: uuid.UUID, approver_handle: str
) -> AcceptCard:
    """主分支保护 (spec §4.4): record one vote toward this card's accept.
    Idempotent per (card, approver); AI cannot vote in collaborative mode."""
    card = await self._card_or_404(card_id)
    # Votable while the card is still live (incl. behind the gate / in a
    # merge-conflict retry); decided or gate-failed cards are closed.
    if card.status not in (
        AcceptStatus.pending,
        AcceptStatus.pending_gate,
        AcceptStatus.conflict,
    ):
        raise ValidationError(say("reviewEndedNoApprove"))
    topic = await self._topic_or_404(card.topic_id)
    project = await self._projects.get(topic.project_id)
    self._forbid_ai(project, approver_handle, say("reviewActionApprove"))
    await self._repo.add_approval(card_id, approver_handle)
    return card


async def mark_ready(
    self: pkg.AcceptService, room_id: uuid.UUID, task_id: uuid.UUID
) -> dict:
    """Mark this task's PR ready; acceptance remains a separate human action."""
    task = await TaskService(self._session).require_in_room(room_id, task_id)
    if task.status != TaskStatus.open or not task.branch_name:
        raise ValidationError(say("taskEndedOrNoBranch"))
    topic = await self._topic_or_404(room_id)
    if topic.status == TopicStatus.archived:
        raise ValidationError(say("topicArchivedCannotReview"))
    if task.pr_number is None:
        return {"ready": False, "reason": "任务还没有 PR，请先提交并同步代码"}
    client = await self._app_pr_client(topic)
    if client is None:
        return {"ready": False, "reason": "这个项目没有绑定 GitHub"}
    from app.domain.review.github_pr import GitHubPRRateLimited

    try:
        view = await client.pr_view(task.pr_number)
    except GitHubPRRateLimited:
        return {
            "ready": False,
            "reason": (
                "GitHub API 请求额度暂时用尽，PR 尚未标记为可评审；额度恢复后请重试。"
            ),
        }
    if view.get("draft"):
        node_id = str(view.get("node_id") or "")
        if not node_id:
            raise ValidationError(say("githubNoPrNodeId"))
        await client.mark_ready_for_review(node_id)
    return {
        "ready": True,
        "already": not bool(view.get("draft")),
        "pr_number": task.pr_number,
        "pr_url": task.pr_url,
    }


async def redescribe(
    self: pkg.AcceptService,
    place_id: uuid.UUID,
    *,
    actor: str,
    change_subject: str | None = None,
    change_body: str | None = None,
) -> AcceptCard:
    """更正这张卡的描述 —— and rewrite the PR from it in the same breath.

    **Why this may be corrected while `Cheese-Task:` may not.** A delivery
    claim is an ASSERTION OF FACT about who wrote the code; letting it be
    edited after filing is letting somebody put another agent's name on a
    change, and the wrong name in permanent history reads exactly like the
    right one. A description is an EXPLANATION of the change, and having a
    reviewer say "that reasoning is wrong" is what review IS. Refusing to
    correct it does not protect history — it guarantees the correction
    happens on the PR page only, and main receives the sentence everyone
    already agreed was false. PR #735 是活例子：评审把 PR 正文改对了，
    `1c298199a` 里留下的仍是递卡那一刻的快照。

    The PR is rewritten from the card, never read back into it. The card is
    the single source of both texts, so they cannot disagree — and the
    trailers (`Cheese-Task`, `Requested-by`, `Cheese-Agent`) stay something
    the platform asserts rather than something anybody can retype in a
    GitHub textarea.

    Only while the card is `pending`. Once it is accepted the commit is
    already in main and there is nothing left to correct here; that case
    belongs in a correction the room records, not in a row nobody reads
    again.
    """
    cards = await self._repo.list_live_for_places(
        [place_id], statuses=(AcceptStatus.pending,)
    )
    if not cards:
        raise ValidationError(say("reviewNoPendingCard"))
    card = cards[0]
    topic = await self._topic_or_404(card.topic_id)
    before_subject, before_body = card.change_subject, card.change_body or ""

    subject = (change_subject or "").strip()
    if subject:
        try:
            card.change_subject = commit_message.check_subject(subject)
        except commit_message.InvalidSubject as exc:
            raise ValidationError(exception_text(exc)) from exc
    if change_body is not None:
        card.change_body = change_body.strip() or None
    if (card.change_subject, card.change_body or "") == (
        before_subject,
        before_body,
    ):
        return card
    await self._session.flush()

    if card.pr_number is not None:
        client = await self._app_pr_client(topic)
        if client is not None:
            who = await identity.attribution(self._session, topic, card=card)
            view = await client.pr_view(card.pr_number)
            await pr_publish.sync_pr_text(
                client,
                view,
                title=pr_text.change_subject(card, topic),
                body=pr_text.pr_body(topic, "", card, who),
            )
    # 留痕：谁在什么时候把描述从什么改成了什么。这条入口的存在本身需要可追溯，
    # 否则它就是一条能悄悄改「这次改动会在历史里说什么」的路。
    self._notify_merge_result(
        topic,
        say("cardRedescribed", actor=actor),
        meta=notice(
            EVENT_CARD_REDESCRIBED,
            severity=SEVERITY_INFO,
            who=WHO_CHEESE,
            detail=say(
                "cardRedescribedDetail",
                beforeSubject=before_subject or say("emptyValue"),
                afterSubject=card.change_subject or say("emptyValue"),
                beforeBody=before_body or say("emptyValue"),
                afterBody=card.change_body or say("emptyValue"),
            ),
            detail_label=say("labelWhatChanged"),
        ),
    )
    await self._session.flush()
    await self._session.refresh(card)
    return card


async def push_fix(
    self: pkg.AcceptService, place_id: uuid.UUID, *, drop_dependency: bool = False
) -> dict:
    """Observe the branch the CLI already pushed directly to the forge."""
    cards = await self._repo.list_live_for_places(
        [place_id], statuses=(AcceptStatus.pending, AcceptStatus.conflict)
    )
    cards = [c for c in cards if c.pr_number is not None]
    if drop_dependency:
        from app.domain.project.forge import default_branch
        from app.domain.review import github_pr

        task = await TaskService(self._session).get(place_id)
        if task is None or task.pr_number is None:
            raise ValidationError(say("taskNoPrForDependency"))
        topic = await self._topic_or_404(task.room_id)
        for card in cards:
            await self._pr_repo_of(card, topic)
        publisher = await self._app_pr_client(topic)
        if publisher is None:
            raise ValidationError(say("repositoryUnavailableForDependency"))
        base = await default_branch(task.project_id, self._session)
        try:
            await publisher.update_pr(task.pr_number, base=base)
            status = await publisher.pr_status(task.pr_number)
        except (github_pr.GitHubPrError, github_pr.GitHubPRError) as exc:
            raise ValidationError(
                say("prBaseUpdateFailed", reason=exception_text(exc))
            ) from exc
        if status.base_ref != base:
            raise ValidationError(say("repositoryBaseUnconfirmed"))
        pushed = await self._sync_task_dependency_target(
            task, status, drop_dependency=True
        )
        for card in cards:
            if pushed or card.pr_head_sha != status.head_sha:
                pushed = True
                await self._dismiss_stale_accept(card=card, topic=topic)
                card.pr_head_sha = status.head_sha
                card.merge_state = None
                card.rebase_count = 0
                notes.clear(card)
        await self._session.flush()
        return {
            "pushed": pushed,
            "pr_number": task.pr_number,
            "pr_url": task.pr_url,
            "reason": "" if pushed else "任务已无依赖，PR 没有新提交",
        }
    if not cards:
        return {"pushed": False, "reason": "这个话题手上没有骑着 PR 的验收卡"}
    card = cards[0]
    assert card.pr_number is not None  # filtered above; for the type checker
    topic = await self._topic_or_404(card.topic_id)
    try:
        owner, repo = await self._pr_repo_of(card, topic)
    except ValidationError as exc:
        return {"pushed": False, "reason": str(exc)}

    creds, reason = await self._pr_poll_credentials(card, topic)
    if creds is None:
        return {"pushed": False, "reason": f"拿不到可用的 GitHub 凭据：{reason}"}

    from app.domain.review import github_pr

    client = await self._status_client(topic.project_id)
    try:
        status = await client.pull_request_status(
            owner=owner, repo=repo, number=card.pr_number, token=creds.read
        )
        retargeted = await self._sync_dependency_target(card, status)
        pushed = retargeted or status.head_sha != card.pr_head_sha
        if pushed:
            await self._dismiss_stale_accept(card=card, topic=topic)
            card.pr_head_sha = status.head_sha
            card.merge_state = None
            card.rebase_count = 0
            notes.clear(card)
    except (github_pr.GitHubPrError, github_pr.GitHubPRError) as exc:
        # Same reasoning as the poll path: say it on the card, because the
        # person waiting is looking at the card and not at a log file.
        self._note_poll_failed(card, exc)
        await self._session.flush()
        return {"pushed": False, "reason": f"GitHub 暂时不通：{exc}"}
    await self._session.flush()
    return {
        "pushed": pushed,
        "pr_number": card.pr_number,
        "pr_url": card.pr_url,
        "reason": "" if pushed else "分支上没有 PR 还不知道的提交",
    }


async def reject(
    self: pkg.AcceptService, *, card_id: uuid.UUID, decided_by: str, note: str = ""
) -> AcceptCard:
    card = await self._card_or_404(card_id)
    if card.status != AcceptStatus.pending:
        raise ValidationError(say("reviewEndedNoReturn"))
    if decided_by != card.reviewer_handle:
        raise ForbiddenError(say("returnReviewerOnly"))

    await self._cancel_queued_accept(card)
    card.status = AcceptStatus.rejected
    card.decided_by = decided_by
    card.decided_at = datetime.now(UTC)
    notes.record(card, None, note)

    # Topic stays active on rejection.
    await self._session.flush()
    await self._session.refresh(card)
    return card


async def revoke(
    self: pkg.AcceptService, *, card_id: uuid.UUID, decided_by: str
) -> AcceptCard:
    card = await self._card_or_404(card_id)
    # Accept is revocable (spec §6.3): only an accepted card can be revoked.
    if card.status != AcceptStatus.accepted:
        raise ValidationError(say("revokeAcceptedOnly"))

    # Only the person who accepted it, or someone who manages the project,
    # may revoke — not any arbitrary handle.
    topic = await self._topic_or_404(card.topic_id)
    if decided_by != card.decided_by and not await MemberService(self._session).manages(
        topic.project_id, decided_by
    ):
        raise ValidationError(say("revokeAcceptForbidden"))

    card.status = AcceptStatus.revoked
    card.decided_by = decided_by
    card.decided_at = datetime.now(UTC)

    # 撤销的是这次**验收记录**，不是这次合并 —— PR 已经在 main 上了，git 层面
    # revoke 什么都没撤。只清采纳标记；任务仍然关闭，后续修改用新任务交付。
    #
    # 归档状态一律不动，这是 2026-08-17 的对称面：`TopicService.unarchive` 的
    # docstring 说「取消归档不改写采纳记录，那要用撤回采纳」；反过来同理——
    # 撤回采纳不改写归档状态，那是人的决定（取消归档）。以前这里要把话题拉回
    # active，是因为采纳会顺手归档；采纳不再归档之后，一张卡的撤销没有理由
    # 覆盖某个人「把这个话题收起来」的动作。
    await self._stamp_delivery(card, topic, by=None, at=None)

    await self._session.flush()
    await self._session.refresh(card)
    return card


async def merge_despite_checks(
    self: pkg.AcceptService,
    *,
    card_id: uuid.UUID,
    decided_by: str,
    reason: str = "",
    head_sha: str | None = None,
) -> AcceptCard:
    """人明知规则没满足，仍然决定合并——**署名的**显式出口（人工放行）。

    为什么必须有：红着合有时候是对的。CI 基础设施抽风、与本次改动无关的既有
    失败、赶时间的热修——真正不能接受的不是「红着合」，而是**没有人做过这个
    决定**。平台自己的默认因此是拒绝（`_merge_pr_for_accept`：合并态不是
    clean/unstable 就不合），而这条出口是另一半：**显式放行，且放行必须
    签字**。

    平台不重算「这段代码好不好」：它只把 forge 的结论如实呈上，然后让一个
    **具名的人**在上面按手印。它记什么：谁、什么时候、**当时的检查到底是
    什么状态**（现读一次，读不到就如实写读不到——但绝不因此拒绝放行，凭据坏
    了不该把人锁在门外）、以及人自己写的理由。

    谁能点 (#718)：项目分支保护的人工放行名单（`override_handles`，没配置
    = 项目所有者 + 团队的所有者和管理员）。芝士被 `_forbid_ai` 挡在外面
    （跟 accept/approve/void 同一条线），路由也**故意不进** `app/main.py`
    的 `_CHEESE_WRITE_PATHS`——
    照 `void` 的先例：不进白名单本身拦不住任何东西（没列进去的写路由压根不
    过那个中间件），真正拦住芝士的是这里的 `_forbid_ai` 加路由上的登录校验。

    放行**放的是规则，不是眼睛**：它跟采纳一样要声明「我看的是哪一版」
    （`_seen_head_or_refresh`）。签字的人要为一段具体的代码背书，屏幕上那版
    已经不在了、或者卡面压根没显示过任何版本的时候，这个签名就落到了别的东西
    上。
    """
    from app.domain.project.protection import branch_protection_of

    card = await self._card_or_404(card_id)
    if card.status != AcceptStatus.pending or card.pr_merged_at is not None:
        raise ValidationError(say("overridePendingOnly"))
    if card.pr_number is None:
        raise ValidationError(say("noMergeablePr"))

    topic = await self._topic_or_404(card.topic_id)
    project = await self._projects.get(topic.project_id)
    self._forbid_ai(project, decided_by, say("reviewActionLetThrough"))

    protection = branch_protection_of(project)
    if protection.override_handles is not None:
        allowed = set(protection.override_handles)
    else:
        allowed = (
            {decided_by}
            if await MemberService(self._session).manages(topic.project_id, decided_by)
            else set()
        )
    if decided_by not in allowed:
        raise ForbiddenError(say("overrideListOnly"))
    # 骑着 PR 的卡在这里必然带着一个被展示过的 sha：卡面从没显示过 head 的
    # （刚递、轮询器还没镜像）会被刷新并要求重看，而不是拿现读的 head 去合。
    seen_head = await self._seen_head_or_refresh(
        card, topic, head_sha, say("verbMergeAnyway")
    )
    assert seen_head is not None  # PR lane; the guard above rules None out

    forge = await self._resolve_forge(topic.project_id, card=card)
    return await forge.merge_despite_checks(
        self, card, topic, decided_by, seen_head=seen_head, reason=reason
    )


async def _override_github_checks(
    self: pkg.AcceptService,
    card: AcceptCard,
    topic: Topic,
    decided_by: str,
    *,
    seen_head: str,
    reason: str,
) -> AcceptCard:
    assert card.pr_number is not None  # The shared override entry requires a PR.
    creds, why = await self._pr_poll_credentials(card, topic)
    if creds is None:
        raise ValidationError(say("overrideNoGithubCredentials", why=why))

    owner, repo = await self._pr_repo_of(card, topic)
    client = await self._status_client(topic.project_id)
    status = await client.pull_request_status(
        owner=owner, repo=repo, number=card.pr_number, token=creds.read
    )
    if not status.merged:
        dependency = await self._dependency_block_reason(card, status)
        if dependency:
            raise ValidationError(dependency)
    # 留痕用，不是门禁：读一次「此刻检查是什么状态」，读不到也照样放行。
    state: str | None = None
    try:
        state, tail = await client.check_state(
            owner=owner, repo=repo, ref=seen_head, token=creds.read
        )
        checks_at_merge = say(
            "forceMergedChecks",
            state=state,
            line=tail.splitlines()[0] if tail else "",
        )
    except Exception as exc:  # noqa: BLE001 — a broken read must not lock a human out
        logger.warning("force-merge check read failed for card %s: %s", card.id, exc)
        state = None
        checks_at_merge = say("forceMergedChecksUnread")
    # 读到的状态决定这句话怎么写：全绿时说「明知未全绿」是往历史里写一条从没
    # 发生过的决定（PR #520 真的这么记了一条）。
    verdict = _force_merge_verdict(state)

    number = card.pr_number
    who = await identity.attribution(
        self._session, topic, card=card, decided_by=decided_by
    )
    from app.domain.project.forge import ensure_author_email

    if who.author:
        await ensure_author_email(topic.project_id, self._session, who.author.email)
    result = await client.merge_pull_request(
        owner=owner,
        repo=repo,
        number=number,
        token=creds.write,
        commit_title=pr_text.merge_commit_title(card, topic, number),
        commit_message=pr_text.merge_commit_message(topic, decided_by, card, who),
        # 放行合的也是人看到的那个 commit：head 变了 GitHub 409，卡刷新。
        sha=seen_head,
    )
    if result.stale_head:
        await self._refresh_stale_card(
            card, topic, live_head="", action=say("verbMergeAnyway")
        )
        raise ValidationError(say("prNewCommitsDuringOverride", number=number))
    if result.queued:
        await self._record_queue_entry(card, decided_by)
        return card
    if result.sha is None:
        raise ValidationError(
            say(
                "githubMergeRefused",
                pr=number,
                reason=result.blocked_reason or say("reasonUnstated"),
            )
        )

    now = datetime.now(UTC)
    stamp = now.strftime("%Y-%m-%d %H:%M UTC")
    tail_reason = (
        say("forceMergedReason", reason=reason.strip()) if reason.strip() else ""
    )
    headline = (
        f"{FORCE_MERGED_PREFIX}：<@{decided_by}> 于 {stamp} 人工放行合并"
        f"（{verdict}；合并时检查状态：{checks_at_merge}）{tail_reason}"
    )
    card.pr_merged_at = now
    await self._mark_task_merged(card, delivered_head=seen_head)
    card.pr_head_sha = result.sha
    await self._repo.add_approval(card.id, decided_by)
    card.decided_by = decided_by
    card.decided_at = now
    await self._finish_pr_accept(card=card, topic=topic, headline=headline)
    logger.warning(
        "card %s: PR #%s force-merged by %s (checks: %s)",
        card.id,
        number,
        decided_by,
        checks_at_merge,
    )
    self._notify_merge_result(
        topic,
        say("forceMerged", actor=f"<@{decided_by}>", pr=number),
        meta=notice(
            EVENT_FORCE_MERGED,
            severity=SEVERITY_WARN,
            who=WHO_HUMAN,
            detail=say(
                "forceMergedDetail",
                verdict=verdict,
                checks=checks_at_merge,
                reason=tail_reason,
                url=card.pr_url or "",
            ),
            detail_label=say("labelForceMergeRecord"),
        ),
    )
    return card


async def void(
    self: pkg.AcceptService, *, card_id: uuid.UUID, decided_by: str, note: str = ""
) -> AcceptCard:
    """人工作废一张未决的验收卡 (pending_gate 孤儿卡出口, 2026-08-11).

    这是**唯一**能把非终态卡强制收尾的人工动作。它存在的理由是 `create_card`
    的互斥：一张卡卡在 `pending_gate` / `conflict` 上，整个话题就再也递不出
    第二张卡，而 accept/reject/revoke/reassign 四条路由对这些状态全部是拒绝
    的——出口是零。

    ⚠️ **它把卡置为终态，不是"放行到 pending"**。放行等于让卡面的绿勾替一段
    从没被检查过的代码背书；作废 + 重递效果一样而且安全，这条区别是本功能的
    设计前提，不要"优化"掉。

    授权：卡上的验收人、项目所有者、团队的所有者和管理员。它是授权类动作，所以芝士在
    collaborative 模式下被 `_forbid_ai` 挡住（跟 accept/approve 同一条线）
    —— 路由也**故意不进** `app/main.py` 的 `_CHEESE_WRITE_PATHS`。
    """
    card = await self._card_or_404(card_id)
    if card.status not in archive.OPEN_CARD_STATUSES:
        raise ValidationError(say("reviewEndedNoVoid"))

    topic = await self._topic_or_404(card.topic_id)
    project = await self._projects.get(topic.project_id)
    self._forbid_ai(project, decided_by, say("reviewActionVoid"))

    if decided_by != card.reviewer_handle and not await MemberService(
        self._session
    ).manages(topic.project_id, decided_by):
        raise ForbiddenError(say("voidForbidden"))

    await self._cancel_queued_accept(card)
    was = card.status
    reason = say("cardVoidedReason", note=note.strip()) if note.strip() else ""
    headline = (
        f"{VOIDED_PREFIX}：<@{decided_by}> 作废于状态「{was}」。"
        f"话题可以重新提交审阅。{reason}"
    )
    if archive.pr_left_open(card):
        # 跟归档收敛同一条产品判断 (review/archive.py 的模块 docstring)：平台
        # 不拿别人的 token 去关别人名下的 PR。停止跟进 + 留痕。
        headline = (
            f"{VOIDED_PREFIX}：<@{decided_by}> 作废了这次审阅，平台已停止跟进 "
            f"PR #{card.pr_number}。PR 未合并，仍在 GitHub 上打开，需要人决定"
            f"合并还是关闭：{card.pr_url or '（无链接）'}{reason}"
        )
    card.status = AcceptStatus.revoked
    notes.record(card, notes.NoteCode.voided, archive.prefix_note(card.note, headline))
    # 只在空的时候补：作废人始终写在 note 里，已有的决议痕迹不覆盖。
    if card.decided_by is None:
        card.decided_by = decided_by
    if card.decided_at is None:
        card.decided_at = datetime.now(UTC)

    await self._session.flush()
    await self._session.refresh(card)

    # 作废的是这张卡，落点就是这张卡（结论 14）：房间主线那一档只留给为房间
    # 本身递的卡（`task_id` 空）。
    landed = landing(
        EventAbout.task if card.task_id is not None else EventAbout.room,
        project_id=topic.project_id,
        room_id=topic.id,
        task_id=card.task_id,
    )
    await BlockRepository(self._session).add(
        project_id=landed.project_id,
        conversation_id=landed.conversation_id,
        author="cheese",
        author_type=AuthorType.platform,
        content=say("cardVoided", actor=f"<@{decided_by}>"),
        kind=BlockKind.event,
        meta={
            "platform": True,
            **notice(
                EVENT_CARD_VOIDED,
                severity=SEVERITY_INFO,
                who=WHO_CHEESE,
                detail=say("cardVoidedDetail", state=f"{was}", reason=reason),
                detail_label=say("labelVoidNote"),
            ),
        },
    )
    return card
