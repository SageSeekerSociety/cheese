"""One card's poll: read the live status, mirror the verdict, send the events the
「谁的活」 table names, and merge a card whose auto-merge is armed."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from app.core.sentences import say
from app.domain.notice_meta import EVENT_MERGE_WITHHELD
from app.domain.review import merge_state, notes, pr_signals
from app.domain.review import services as pkg
from app.domain.review.github_pr_status import merge_actor
from app.domain.review.merge_state import RED_CONCLUSIONS, MergeVerdict
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.review.nudges import _ci_passed_nudge
from app.domain.review.services._shared import (
    _REQUIRED_CHECK_GRACE_MINUTES,
    EVENT_ACCEPT_DONE,
    SEVERITY_INFO,
    SEVERITY_WARN,
    WHO_HUMAN,
    WHO_PLATFORM,
    _GitHubCredentials,
    notice,
)
from app.domain.review.services.merge_queue import _write_merge_mirror
from app.domain.topic.models import Topic

logger = logging.getLogger(__name__)

#: 入队后多久还没有这个条目的 merge_group run，就在卡上标一次、告诉验收人。
QUEUE_STALL_MINUTES = 30

#: 卡上那句话的固定开头：既是文案，也是「已经标过」的判据 —— 不加字段、不加迁移。
QUEUE_STALL_MARK = "已在合并队列"


def _stall_text(minutes: int) -> str:
    return f"{QUEUE_STALL_MARK} {minutes} 分钟，还没轮到这个条目的 merge_group"


def _clear_stall_mark(card: AcceptCard) -> None:
    """Strip the stall sentence `annotate` put in front of the note."""
    note = card.note or ""
    if QUEUE_STALL_MARK not in note:
        return
    head, _, rest = note.partition("；")
    if QUEUE_STALL_MARK in head:
        notes.record(card, card.note_code, rest)


async def _watch_queue_stall(
    self: pkg.AcceptService,
    *,
    card: AcceptCard,
    topic: Topic,
    owner: str,
    repo: str,
    creds: _GitHubCredentials,
    client,
    now: datetime | None = None,
) -> None:
    """卡在队列里：超过阈值还没有 merge_group run → 标一次并通知验收人；run
    起来了就把那句话清掉。入队时刻读 `decided_at`（`_record_queue_entry` 写）。"""
    lookup = getattr(client, "merge_group_run_started", None)
    entered = card.decided_at
    if lookup is None or entered is None or not card.pr_number:
        return
    marked = QUEUE_STALL_MARK in (card.note or "")
    now = now or datetime.now(UTC)
    if entered.tzinfo is None:
        entered = entered.replace(tzinfo=UTC)
    if not marked and now - entered < timedelta(minutes=QUEUE_STALL_MINUTES):
        return
    try:
        started = await lookup(
            owner=owner,
            repo=repo,
            number=card.pr_number,
            token=creds.read,
            since=entered,
        )
    except Exception:  # 只读提示，读不到不该拖垮轮询
        logger.warning("card %s: merge_group run lookup failed", card.id, exc_info=True)
        return
    if started:
        _clear_stall_mark(card)
        return
    if marked:
        return
    minutes = int((now - entered).total_seconds() // 60)
    text = _stall_text(minutes)
    notes.annotate(card, text)
    await self._tell_the_reviewer(
        card,
        topic,
        f"PR #{card.pr_number} {text}。",
        meta=notice(
            EVENT_MERGE_WITHHELD,
            severity=SEVERITY_WARN,
            who=WHO_HUMAN,
            detail=card.pr_url or "",
            detail_label=say("labelReason"),
        ),
    )


async def _poll_pr_card(
    self: pkg.AcceptService,
    *,
    card: AcceptCard,
    topic: Topic,
    owner: str,
    repo: str,
    creds: _GitHubCredentials,
    client,
    chat_service,
    runner,
) -> None:
    """The poller's three jobs on one card (#718): mirror the merge state,
    send the events the 「谁的活」 table names, and merge an armed card
    when the rules are satisfied. It merges NOTHING otherwise — accepting
    is the human's click, evaluated at click time."""
    number = card.pr_number
    if number is None:  # already guaranteed by advance_pr_card's guard
        return

    # FIRST: did someone already handle this PR on GitHub? A merged-by-hand
    # PR is invisible to every other signal here, and without this the
    # card polls forever.
    status = await client.pull_request_status(
        owner=owner, repo=repo, number=number, token=creds.read
    )
    if status.merged:
        if card.status == AcceptStatus.rejected:
            # The review remains a return. GitHub establishes a separate
            # fact about the batch, including the head that actually landed.
            card.pr_merged_at = status.merged_at or datetime.now(UTC)
            card.merged_by = merge_actor(status)
            landed = await self._mark_task_merged(card, delivered_head=status.head_sha)
            await self._session.flush()
            if landed:
                self._notify_merge_result(
                    topic,
                    say("acceptDoneAfterReturn", pr=number),
                    task_id=card.task_id,
                    meta=notice(
                        EVENT_ACCEPT_DONE,
                        severity=SEVERITY_INFO,
                        who=WHO_PLATFORM,
                        detail=card.pr_url or "",
                    ),
                )
            return
        await self._settle_external_merge(card=card, topic=topic, status=status)
        return
    if card.status == AcceptStatus.rejected:
        # Observing an external merge must never re-arm, approve, update,
        # or merge a returned delivery, even when its checks are green.
        return
    if status.state == "closed":
        await self._void_closed_pr_card(card=card, topic=topic)
        return

    if card.note_code == notes.NoteCode.waiting_merge_queue:
        if await client.merge_queue_entry(
            owner=owner, repo=repo, number=number, token=creds.read
        ):
            # 队列里这段是和普通路径**一样**的等 CI，只是 PR 的 `mergeStateStatus`
            # 被 GitHub 钉在 BLOCKED、判定算出来没有意义，所以上面那条路不进。可
            # 镜像照旧要一轮刷一次：`checked_at` 是「还在等 CI」这句话的有效期，
            # 冻在入队那一刻，等不到队列检查出结果就先过期了 —— 环境释放判据会当
            # 没人再看这张卡，把机器收掉，而轮询其实一直在看，回头又要叫醒这条线
            # 重建环境、重读文档。只推 `checked_at` 到此刻：state、reasons 说的都还
            # 是入队时那份判定，没有任何一条更新。
            await _write_merge_mirror(
                self,
                card,
                MergeVerdict(
                    state="blocked",
                    reasons=(
                        merge_state.MergeReason(
                            kind="ci_running", detail="等待合并队列检查"
                        ),
                    ),
                ),
                "ci",
                card.pr_head_sha or "",
            )
            await _watch_queue_stall(
                self,
                card=card,
                topic=topic,
                owner=owner,
                repo=repo,
                creds=creds,
                client=client,
            )
            await self._session.flush()
            return
        # Removal can mean failed checks or a human cancellation. Never
        # re-enqueue; but the card still reads 「已采纳」, so note it and wake
        # the task's 芝士 instead of leaving it for someone to notice.
        card.auto_merge_armed_by = None
        card.auto_merge_armed_at = None
        _clear_stall_mark(card)
        await self._note_left_merge_queue(card=card, topic=topic)
        await self._session.flush()
        return

    if await self._sync_dependency_target(card, status):
        return

    live = status.head_sha
    if card.pr_head_sha != live:
        if card.pr_head_sha:
            # 新提交作废已有的采纳 (#718, dismiss_stale): the head the
            # reviewer saw moved. Clear what the old head earned —
            # approvals and the auto-merge arm — and tell the reviewer.
            await self._dismiss_stale_accept(card=card, topic=topic)
            # Whatever the note said, it described the old commit.
            notes.clear(card)
        card.pr_head_sha = live
        await self._session.flush()

    verdict, who, protection, enforces, runs = await self._mirror_pr_verdict(
        card,
        topic=topic,
        owner=owner,
        repo=repo,
        creds=creds,
        client=client,
        status=status,
        ref=live,
    )

    # —— 按表发事件，每件各排一条待发，谁都不许把别人挡掉 (pr_signals) ——
    #
    # 收集途中读 GitHub 失败，不能把已经排好队的其它待发一起丢掉。错误推迟
    # 到发完再抛：卡上照样记下这次轮询出过错（advance_pr_card 的 except），
    # 而已经排好的那条已经送到。
    pending: list[pr_signals.PendingNudge] = []
    deferred: Exception | None = None
    kinds = {r.kind for r in verdict.reasons}
    # 红检查按 runs 本身判，不按 verdict 的 reasons：一个和 main 冲突的 PR
    # 的 verdict 是 dirty（冲突最优先），但它上面的红检查照样是芝士要修的
    # 事 —— 三件事互不蕴含，谁都不许把别人吞掉（pr_signals 的规矩）。
    # 词表用 `merge_state.RED_CONCLUSIONS`：`cancelled` 不算红（它没有结论），
    # 一个只剩「已取消」的 PR 不该把人叫起来说「CI 未通过」——卡面照旧标出来。
    has_red_check = any(
        r.conclusion in RED_CONCLUSIONS for r in runs if r.status == "completed"
    )
    checks_red = has_red_check or bool(
        kinds & {"required_check_failed", "check_failed"}
    )
    if not checks_red and card.note_code is notes.NoteCode.checks_failed:
        # 同一个 head 上的红已经不在了（重跑转绿、被顶替的取消运行收掉了）：
        # 「检查未通过」那句说的是过去。不清它，看板一直摆在「修复检查」、验收
        # 人的黄灯不亮 —— head 没动，上面换 head 时那次清理永远轮不到它。
        notes.clear(card)
    if checks_red:
        # 检查红了 → 事件到做活的 agent，带哪个检查红、日志怎么取。
        # `check_state` 是取失败详情（job 链接 + 日志片段）的那条路。
        try:
            state_word, tail = await client.check_state(
                owner=owner, repo=repo, ref=live, token=creds.read
            )
            if state_word == "failure":
                ci = self._ci_nudge(
                    card=card, tail=tail, stage="CI", owner=owner, repo=repo
                )
                if ci is not None:
                    pending.append(ci)
        except Exception as exc:  # 先发完，再抛
            deferred = exc
    elif merge_state.all_checks_passed(runs) and not kinds & {
        "required_check_missing",
        "ci_running",
    }:
        # 绿了也回去找做活的芝士（按 head 只说一次），它就不用自己轮询 CI。
        # 必跑检查还没报到、还有检查在跑，都不算绿。
        passed = _ci_passed_nudge(card=card)
        if passed is not None:
            pending.append(passed)
    try:
        review = await self._review_nudge(
            card=card,
            owner=owner,
            repo=repo,
            creds=creds,
            client=client,
            status=status,
        )
        if review is not None:
            pending.append(review)
    except Exception as exc:  # 见上：先发完，再抛
        deferred = exc
    if verdict.state == "dirty":
        conflict = self._conflict_nudge(card=card, status=status)
        if conflict is not None:
            pending.append(conflict)
    # GitHub 上那行标题还是不是这张卡要落的那一行。不是就停机叫人，而且**绿了
    # 也不算绿**：`_notify_ready` 说「可以合了」、自动合布防真的去合，两条都得
    # 拦住 —— 合下去落进历史的是仓库检查没评判过的字（`pr_text` 的那条注释）。
    title = self._title_nudge(card=card, topic=topic, status=status)
    if title is None and card.note_code is notes.NoteCode.pr_title_disagrees:
        # 两行又对上了（人用 `cheese_card(action=describe)` 把卡片的 subject 改成了该落的那
        # 一行、或者干脆把 GitHub 上的标题改回来了）：「标题和卡片不一致，合并
        # 已暂停」那句说的是过去。改 PR 标题不动 head，上面换 head 时那次清理
        # 永远轮不到它 —— 不清，卡面一直亮着红灯，而同一次轮询已经在下边报了
        # 「可以合了」，同屏两句话互相打脸。放在 `_dispatch_nudges` 之前清，
        # 卡面空下来，它才会按全部待发里最重的那条重写（此刻没有比它更重的）。
        notes.clear(card)
    if title is not None:
        pending.append(title)
    await self._dispatch_nudges(
        card=card,
        topic=topic,
        pending=pending,
        chat_service=chat_service,
        runner=runner,
    )
    if deferred is not None:
        await self._session.flush()  # the mirror and dispatched ledger keep
        raise deferred

    if verdict.state == "clean" and title is None:
        # CLEAN → 通知验收人（按 head 去重）。标题分家时不报「可以合了」：
        # 那一刻它合不进去，而上面那条待发已经把该修的事说清楚了。
        await self._notify_ready(card, topic)
    elif verdict.state == "blocked" and "required_check_missing" in kinds:
        # BLOCKED 必跑检查没报到 → 等 CI，不发；超过宽限期转人 ——
        # workflow 改名、被禁用、Actions 断供都长这样，等下去没有尽头，
        # 而出口是叫人，绝不因为等腻了就自动合并。
        if self._required_absence_overdue(card):
            missing = ", ".join(
                sorted(
                    {
                        name
                        for r in verdict.reasons
                        if r.kind == "required_check_missing"
                        for name in r.checks
                    }
                )
            )
            await self._note_needs_human(
                card=card,
                topic=topic,
                reason=say(
                    "mergeWithheldCheckMissing",
                    checks=missing,
                    minutes=_REQUIRED_CHECK_GRACE_MINUTES,
                ),
                explain=say("mergeWithheldCheckMissingWhy"),
            )
    elif verdict.state == "behind":
        # BEHIND（strict 才出现）→ 平台自己 update-branch；撞冲突的话
        # 下一拍这个 PR 就是 dirty，冲突事件自然转给 agent。
        await self._update_behind_branch(
            card=card,
            topic=topic,
            owner=owner,
            repo=repo,
            creds=creds,
            client=client,
        )
        return

    if (
        card.auto_merge_armed_by
        and verdict.state in ("clean", "unstable")
        and title is None
    ):
        await self._merge_armed_card(
            card=card,
            topic=topic,
            owner=owner,
            repo=repo,
            creds=creds,
            client=client,
            protection=protection,
            chat_service=chat_service,
            runner=runner,
            opened_by=status.opened_by,
        )
        return
    await self._session.flush()


async def _rebase_onto_base(
    *, client, owner: str, repo: str, number: int, token: str
) -> bool:
    """Merge the PR's base branch into its head — the forge's Update branch.

    `token` is the WRITE mint: this PUSHES a merge of base onto the PR branch
    (the read mint has no `contents:write`; PR #575/#582 froze on that).
    False is a non-fatal refusal — already current, the head moved under us,
    or a conflict — and each caller says what that means for it.

    Shared by the poller (BEHIND in the merge-state verdict) and by
    `mark_ready` (before the ready flag starts the branch CI, so that CI reads
    the workflows on main today rather than the ones the branch was cut from).
    """
    return await client.update_branch(
        owner=owner, repo=repo, number=number, token=token
    )


async def _update_behind_branch(
    self: pkg.AcceptService,
    *,
    card: AcceptCard,
    topic: Topic,
    owner: str,
    repo: str,
    creds: _GitHubCredentials,
    client,
) -> None:
    """BEHIND is the platform's move: GitHub's Update branch, capped.

    绿必须绿在当前基线上（strict）。各自绿在旧基上的两个 PR 相加可以是红
    的。换基后 head 变化，下一轮从新 CI 重新等起；反复换基追不上 main 就
    叫人（上限 3，芝士推新提交时清零）。"""
    if card.rebase_count >= 3:
        await self._note_needs_human(
            card=card,
            topic=topic,
            reason=say("mergeWithheldBehind"),
            explain=say("mergeWithheldBehindWhy"),
        )
        await self._session.flush()
        return
    updated = await _rebase_onto_base(
        client=client,
        owner=owner,
        repo=repo,
        number=card.pr_number or 0,
        token=creds.write,
    )
    outcome = (
        "已自动更新分支，等新一轮 CI。" if updated else "自动更新分支被拒，下一轮重试。"
    )
    card.rebase_count += 1
    notes.annotate(card, f"基线落后于 main，{outcome}")
    await self._session.flush()
