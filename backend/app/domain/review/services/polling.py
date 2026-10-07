"""The PR poller and snapshot refresh: advance/refresh one card, mirror
the merge state, the seen-head guard, credentials and task closing."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, NoReturn, cast

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.core.errors import ValidationError
from app.core.sentences import say
from app.domain.review import (
    notes,
    pr_signals,
)
from app.domain.review.models import (
    AcceptCard,
    AcceptStatus,
)
from app.domain.review.repositories import AcceptCardRepository
from app.domain.review.services import poll_claim
from app.domain.room_task.checkouts import after_close
from app.domain.room_task.models import TaskStatus
from app.domain.room_task.services import TaskService
from app.domain.topic.models import Topic, TopicStatus

if TYPE_CHECKING:  # `github_pr` stays a lazy import at every call site
    pass

from app.domain.review import services as pkg
from app.domain.review.services._shared import (
    _REQUIRED_CHECK_GRACE_MINUTES,
    EVENT_ACCEPT_DONE,
    SEVERITY_INFO,
    WHO_PLATFORM,
    ProjectRepository,
    _GitHubCredentials,
    _never_shown_message,
    _stale_view_message,
    logger,
    notice,
)


def _seen_head(card: AcceptCard, head_sha: str | None, action: str) -> str | None:
    """合的是**人看到的**那个 commit：核对请求声明的 head，并把它交回去用。

    `head_sha` 是前端渲染这张卡时卡面上的 head（`merge_state.head_sha`）。
    它必须仍然是卡当前的 `pr_head_sha`——不一致意味着轮询器在渲染与点击之间
    把卡刷到了新 commit，而屏幕上那份还是旧的：点下去合的会是一段**没有人
    看过**的代码 (`advance_pr_card` 每 60s 跑一次，这个窗口天天都在)。
    `dismiss_stale` 保护不了它，那条只清批准票，而采纳本身就是一票。

    None 与 None 相等只在**没有 GitHub PR 的那条 lane 上**成立：卡面显示的
    就是「没有 sha」，合的是 diff 视图展示的那条分支本身，没有哪一版可以过
    时。骑着 PR 的卡不是这样——那种情况下「卡上没有 sha」意味着还不知道要合
    哪个 commit，`_seen_head_or_refresh` 在进这里之前就把它拦下了。反过来，
    卡上有 head 而请求什么都不带（老客户端）就是不相等，照样拒——不带 sha
    不是绕过这道闸的方式。

    返回值是**请求带的**那个 sha，调用方拿它去调合并 API：GitHub 的 sha
    参数会在点击瞬间再拦一次漂移（409）。
    """
    seen = (head_sha or "").strip() or None
    if seen != (card.pr_head_sha or None):
        raise ValidationError(_stale_view_message(card.pr_number, action))
    return seen


async def _seen_head_or_refresh(
    self: pkg.AcceptService,
    card: AcceptCard,
    topic: Topic,
    head_sha: str | None,
    action: str,
) -> str | None:
    """`_seen_head`，外加「PR 上的卡必须先有过一个展示出来的 head」这道闸。

    骑着 PR 的卡在 `pr_head_sha` 还是空的时候（刚递上来、轮询器 60s 才跑一
    次），卡面从未写出过任何 sha。此时放行等于让下游拿**现读 GitHub** 的
    head 去合，而那个 commit 从来没有在任何界面上显示过——采纳、人工放行、
    自动合布防三个入口都会走到那里，是同一个洞。

    所以这不是「没有版本可以过时」，是「还不知道要合什么」：把当前 head 镜像
    到卡上，让人重新看一眼，看过的那一版才谈得上被采纳。没有 PR 的本地 lane
    不进这道闸——那条路合的就是 diff 视图展示的分支本身。
    """
    if card.pr_number is not None and not (card.pr_head_sha or "").strip():
        await self._refresh_never_shown_card(card, topic, action)
    return self._seen_head(card, head_sha, action)


async def _refresh_never_shown_card(
    self: pkg.AcceptService, card: AcceptCard, topic: Topic, action: str
) -> NoReturn:
    forge = await self._resolve_forge(topic.project_id, card=card)
    await forge.refresh_unseen_head(self, card, topic, action)


async def _refresh_github_unseen_head(
    self: pkg.AcceptService, card: AcceptCard, topic: Topic, action: str
) -> NoReturn:
    """把 PR 当前的 head 镜像到一张从没显示过 sha 的卡上，然后要求重看。

    读 head 是尽力而为：读不到就刷新一张没有 head 的卡（轮询器下一跳会补
    上），但**绝不**因此放行——放行的前提是人看过某一版，读不到 head 恰恰
    说明没有任何一版可看。"""

    number = card.pr_number
    assert number is not None  # PR lane only; the caller checked
    live = ""
    creds, why = await self._app_credentials(topic)
    if creds is None:
        logger.warning("card %s: no credentials to read PR head (%s)", card.id, why)
    else:
        try:
            owner, repo = await self._pr_repo_of(card, topic)
            client = await self._status_client(topic.project_id)
            live = await client.pull_request_head_sha(
                owner=owner, repo=repo, number=number, token=creds.read
            )
        except Exception as exc:  # noqa: BLE001 — refuse anyway; refresh with what we have
            logger.warning("card %s: first-look head read failed: %s", card.id, exc)
    await self._refresh_stale_card(
        card,
        topic,
        live_head=live,
        action=action,
        headline=(f"PR #{number} 还没有显示过任何版本，已刷新到最新版本"),
    )
    raise ValidationError(_never_shown_message(number, action))


async def _status_client(self: pkg.AcceptService, project_id: uuid.UUID):
    from app.domain.project.forge import status_client

    return _remote(self, await status_client(project_id, self._session))


def _remote[Client](self: pkg.AcceptService, client: Client) -> Client:
    """A forge client as this service may call it: during a claimed poll,
    one that gives the database connection back before each call. The
    wrapper answers every call the client does, so it stands in as one."""
    if client is None or not self._commits_before_remote:
        return client
    return cast("Client", poll_claim.CommitsBeforeRemote(client, self._session))


async def _refresh_stale_card(
    self: pkg.AcceptService,
    card: AcceptCard,
    topic: Topic,
    *,
    live_head: str,
    action: str = "采纳",
    headline: str | None = None,
) -> None:
    """新提交作废已有的采纳 (#718, dismiss_stale — GitHub 的「Dismiss stale
    pull request approvals」，这里默认开): the head moved out from under
    the reviewer, so the card refreshes — new head, stale mirror dropped,
    approvals cleared (when the project dismisses stale accepts), and the
    auto-merge arm disarmed.

    Written OUTSIDE the accept transaction (its caller is about to raise).
    **Rolls the request transaction back first**, for the same reason as
    `_stop_accept_pr_unavailable`: this request may already hold a row
    lock on the very card the fresh session is about to write, and two
    connections on one row with one waiting on the other is a hang, not a
    refresh. Every attribute needed later is read before the rollback
    (expired attributes reload with sync IO an AsyncSession cannot do)."""
    from app.domain.project.protection import branch_protection_of

    card_id = card.id
    number = card.pr_number
    project_id = topic.project_id
    await self._session.rollback()
    try:
        factory = async_sessionmaker(self._session.bind, expire_on_commit=False)
        async with factory() as session:
            project = await ProjectRepository(session).get(project_id)
            dismiss = branch_protection_of(project).dismiss_stale
            repo = AcceptCardRepository(session)
            fresh = await repo.get(card_id)
            if fresh is None:
                return
            if live_head:
                fresh.pr_head_sha = live_head
            fresh.merge_state = None  # mirrored for the old head — stale
            if dismiss:
                await repo.clear_approvals(card_id)
                fresh.auto_merge_armed_by = None
                fresh.auto_merge_armed_at = None
            notes.record(
                fresh,
                None,
                (headline or f"PR #{number} 有新提交，之前看到的版本已过时")
                + ("；已有的批准一并作废" if dismiss else "")
                + f"，请重新查看后再{action}",
            )
            await session.commit()
    except Exception:  # noqa: BLE001 — the raise this accompanies must fire
        logger.exception("could not refresh stale card %s", card_id)


async def _pr_poll_credentials(
    self: pkg.AcceptService, card: AcceptCard, topic: Topic
) -> tuple[_GitHubCredentials | None, str]:
    """The App's GitHub credentials for this card's PR — the only lane
    left (#718 deleted the personal-token one). Two mints, not one: see
    `_GitHubCredentials` for why reading checks with the write token is a
    403 that presents as a card frozen forever. Never raises — a poll
    tick degrades to "pause and retry"."""
    del card  # one lane now; the signature stays call-site-stable
    return await self._app_credentials(topic)


async def _app_credentials(
    self: pkg.AcceptService, topic: Topic
) -> tuple[_GitHubCredentials | None, str]:
    """The platform App's installation tokens for this project's repo."""
    from app.domain.project.forge import tokens_for_project

    tokens = await tokens_for_project(topic.project_id, self._session)
    if tokens is None:
        return None, "这个项目的代码托管凭据不可用"
    tokens = _remote(self, tokens)
    try:
        write, _ = await tokens.write_token()
        read, _ = await tokens.installation_token()
    except Exception as exc:  # noqa: BLE001 — pause this tick, don't crash
        return None, f"获取代码托管凭据失败（{type(exc).__name__}）"
    return _GitHubCredentials(write=write, read=read), ""


async def advance_pr_card(
    self: pkg.AcceptService, card_id: uuid.UUID, *, chat_service, runner
) -> None:
    """Observe external merges and advance pending PR cards (#718). The
    poller does three things and nothing else: mirror the merge state
    onto the card, send the events the 「谁的活」 table names (deduped
    through the nudge ledger), and merge a card whose auto-merge is armed
    once the rules are satisfied. Called by
    `review/pr_poll.py::poll_open_prs`; never raises for a transient GitHub
    hiccup — the next poll just retries."""
    # A webhook and the reconciliation clock may observe the same card; only
    # one poll may advance it or emit its notifications (`poll_claim`).
    token = await poll_claim.claim(self._session, card_id)
    if token is None:
        return
    self._commits_before_remote = True
    try:
        await _advance_claimed(self, card_id, chat_service=chat_service, runner=runner)
        await poll_claim.release(self._session, card_id, token)
    except Exception:
        await self._session.rollback()
        await poll_claim.release(self._session, card_id, token)
        await self._session.commit()
        raise
    finally:
        self._commits_before_remote = False


async def _advance_claimed(
    self: pkg.AcceptService, card_id: uuid.UUID, *, chat_service, runner
) -> None:
    card = await self._session.get(AcceptCard, card_id, populate_existing=True)
    if card is None:
        return
    if (
        card.status not in (AcceptStatus.pending, AcceptStatus.rejected)
        or card.pr_number is None
    ):
        return
    if card.status == AcceptStatus.rejected:
        tree = (
            await TaskService(self._session).get(card.task_id)
            if card.task_id is not None
            else None
        )
        if (
            card.pr_merged_at is not None
            or tree is None
            or tree.status != TaskStatus.open
        ):
            return
    topic = await self._topic_or_404(card.topic_id)
    forge = await self._resolve_forge(topic.project_id, card=card)
    await forge.poll(self, card, topic, chat_service=chat_service, runner=runner)


async def refresh_stale_pr_snapshots(
    self: pkg.AcceptService, cards: Sequence[AcceptCard]
) -> None:
    """读卡这条路，把卡面上过期的合并态补上。

    卡上的 `merge_state` 是一份**快照**，而前端那颗「采纳」按钮按它亮不亮
    (`TopicAcceptCard.vue` 的 `acceptBlockedTitle`)。快照平时只有两个时候
    重算：轮询器 (`accept_pr_poll_interval_s`，默认 300s)，和点下采纳的那一
    刻。夹在两者之间来读的人只会读到陈旧的一份 —— 实测 CI 在 11:28Z 就全绿
    了，卡到 11:49Z 才写进 `clean`，中间二十来分钟界面上是「检查全绿、按钮
    点不动」；更短的一次是 12:06Z 卡上被写成 `unknown`（GitHub 那一刻还没算
    完 mergeable），而此刻 `/pr-checks` 早已 `mergeable: true`，快照又冻了五
    分钟。界面每 15s 来读一次，读到的却是同一份旧快照，**靠前端轮询自己收敛
    不了**。

    所以给读这条路一个它自己的保证：快照比 `accept_pr_snapshot_floor_s` 还
    旧，就重算一次。地板是必须的 —— 读卡是热点，不能每个读者都触发一次外呼。

    只做一件事：把当前判定重算一遍写回去（具体交给托管方那一侧的能力，
    `Forge.refresh_snapshot`）。轮询器做的另外两件**不做** —— 不发事件、
    不合并 (那是它的职责，`advance_pr_card` 的 docstring)。任何失败都吞掉：
    读卡不能因为 GitHub 抖一下就 500，快照下一轮重试就是。
    """
    for card in cards:
        try:
            await self._refresh_stale_pr_snapshot(card)
        except Exception:  # noqa: BLE001 — a read never fails over a poll
            logger.warning(
                "card %s: refreshing a stale merge snapshot failed",
                card.id,
                exc_info=True,
            )


def _merge_snapshot_age_s(self: pkg.AcceptService, card: AcceptCard) -> float | None:
    """这份快照是多久以前算的；没算过或读不懂就是 None（当作最陈旧）。"""
    state = card.merge_state if isinstance(card.merge_state, dict) else None
    raw = (state or {}).get("checked_at")
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return (datetime.now(UTC) - when).total_seconds()


async def _refresh_stale_pr_snapshot(self: pkg.AcceptService, card: AcceptCard) -> None:
    if card.status != AcceptStatus.pending or card.pr_number is None:
        return
    age = self._merge_snapshot_age_s(card)
    if age is not None and age < settings.accept_pr_snapshot_floor_s:
        return
    topic = await self._topic_or_404(card.topic_id)
    if topic.status == TopicStatus.archived:
        # 归档里的卡不会再被采纳，没必要为它花一次外呼。
        return
    try:
        forge = await self._resolve_forge(topic.project_id, card=card)
    except ValidationError:
        # 托管绑定暂时读不出来。`describe` 那一侧照样把卡发出去（在卡面上
        # 如实说「暂时读不出」），这里也照做：不补快照，轮询器下一跳再试。
        return
    await forge.refresh_snapshot(self, card, topic)


async def _refresh_github_snapshot(
    self: pkg.AcceptService, card: AcceptCard, topic: Topic
) -> None:
    """GitHub 那一侧的重算：读一眼现在的状态，写回卡面。"""
    number = card.pr_number
    assert number is not None  # PR lane only; the caller checked
    owner, repo = await self._pr_repo_of(card, topic)
    creds, reason = await self._pr_poll_credentials(card, topic)
    if creds is None:
        logger.info("card %s: stale snapshot left alone (%s)", card.id, reason)
        return
    client = await self._status_client(topic.project_id)
    status = await client.pull_request_status(
        owner=owner, repo=repo, number=number, token=creds.read
    )
    live = status.head_sha
    if card.pr_head_sha != live:
        # head 动了 —— 那属于「新提交作废已有的采纳」(`dismiss_stale_accept`
        # 会撤掉别人给的批准并发通知) 这个状态迁移，是 `_poll_pr_card` 的事。
        # 浏览器每 15s 读一次卡，读的人可能根本不在乎这张卡：读不替它迁移。
        return
    await self._mirror_pr_verdict(
        card,
        topic=topic,
        owner=owner,
        repo=repo,
        creds=creds,
        client=client,
        status=status,
        ref=live,
    )


async def _advance_github_card(
    self: pkg.AcceptService,
    card: AcceptCard,
    topic: Topic,
    *,
    chat_service,
    runner,
) -> None:
    try:
        owner, repo = await self._pr_repo_of(card, topic)
    except Exception as exc:  # noqa: BLE001 — pause this tick, retry next
        logger.warning("card %s: cannot resolve PR repo: %s", card.id, exc)
        return

    creds, reason = await self._pr_poll_credentials(card, topic)
    if creds is None:
        logger.warning(
            "card %s has no usable GitHub token anymore (%s); "
            "skipping this poll (will retry next tick)",
            card.id,
            reason,
        )
        # Without this the card just sits there forever and looks
        # identical to "CI still running" — no signal anyone's token died.
        if (
            card.status == AcceptStatus.pending
            and card.note_code is not notes.NoteCode.poll_paused
        ):
            notes.record(
                card,
                notes.NoteCode.poll_paused,
                f"轮询暂停，下一轮还会重试：{reason}",
            )
            await self._session.flush()
        return

    # Token is usable again → the pause note is stale. Clearing it here is
    # what makes the pause self-healing: it stops describing a condition
    # that no longer holds, AND it can no longer sit in front of a real CI
    # failure (which is how "轮询暂停" used to swallow CI 失败 notifications
    # — see the 2026-08-10 note in `_ci_nudge`). Only this exact prefix is
    # cleared; 重推失败 /
    # 拒绝合并 / 检查未通过 notes describe live conditions and stay put.
    if card.status == AcceptStatus.pending and card.note_code in (
        notes.NoteCode.poll_paused,
        notes.NoteCode.poll_failed,
    ):
        notes.clear(card)
        await self._session.flush()

    from app.domain.review import github_pr

    client = await self._status_client(topic.project_id)
    try:
        await self._poll_pr_card(
            card=card,
            topic=topic,
            owner=owner,
            repo=repo,
            creds=creds,
            client=client,
            chat_service=chat_service,
            runner=runner,
        )
    except github_pr.GitHubPrError as exc:
        logger.warning(
            "GitHub API hiccup polling card %s: %s — retrying next tick",
            card.id,
            exc,
        )
        # And say it on the CARD. A log line is only readable by whoever has
        # a shell on the host, and the person waiting is looking at a card
        # whose note still says 「等 CI」 — so a poll that fails every tick
        # forever is indistinguishable from checks that are simply slow.
        # That is how #575/#582 sat green-but-unmerged with nothing on
        # screen to explain it. Same treatment the credential branch above
        # already gets, for the same reason.
        if card.status == AcceptStatus.pending:
            self._note_poll_failed(card, exc)
        await self._session.flush()


async def _mark_task_merged(
    self: pkg.AcceptService, card: AcceptCard, *, delivered_head: str | None = None
) -> None:
    if card.task_id is None:
        return
    task = await TaskService(self._session).require_in_room(card.topic_id, card.task_id)
    task.status = TaskStatus.closed
    task.closed_at = task.closed_at or datetime.now(UTC)
    after_close(self._session, task.room_id)
    task.accepted_at = task.accepted_at or datetime.now(UTC)
    task.accepted_by = task.accepted_by or card.decided_by
    if delivered_head and not task.delivered_head:
        task.delivered_head = delivered_head[:64]
    await self._session.flush()


async def _app_pr_client(self: pkg.AcceptService, topic: Topic):  # noqa: ANN202 — GitHubPRClient
    """The App-token client for this project's upstream, or None when the
    project has no GitHub side at all (no installation, or an upstream that
    is not a GitHub https remote)."""
    from app.domain.project.forge import proposal_client

    return _remote(self, await proposal_client(topic.project_id, self._session))


async def note_poll_crashed(
    self: pkg.AcceptService, card_id: uuid.UUID, exc: BaseException
) -> None:
    """Same explanation as `_note_poll_failed`, for a poll that died on
    something other than a GitHub error (the poller's own catch-all).

    Its caller rolled the failed tick back, so this runs on a fresh session
    and is a no-op for a card that has since settled or lost its PR.
    """
    card = await AcceptCardRepository(self._session).get(card_id)
    if card is None or card.status != AcceptStatus.pending:
        return
    self._note_poll_failed(card, exc)
    await self._session.flush()


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
            await self._mark_task_merged(card, delivered_head=status.head_sha)
            await self._session.flush()
            self._notify_merge_result(
                topic,
                say("acceptDoneAfterReturn", pr=number),
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
            return
        # Removal can mean failed checks or a human cancellation. Never re-enqueue.
        card.auto_merge_armed_by = None
        card.auto_merge_armed_at = None
        notes.record(
            card,
            notes.NoteCode.merge_refused,
            f"PR #{number} 已离开合并队列但尚未确认合并，请检查 GitHub 后重新采纳。",
        )
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
    has_red_check = any(
        r.conclusion in ("failure", "timed_out", "cancelled", "action_required")
        for r in runs
        if r.status == "completed"
    )
    if has_red_check or kinds & {"required_check_failed", "check_failed"}:
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
        except Exception as exc:  # noqa: BLE001 — 先发完，再抛
            deferred = exc
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
    except Exception as exc:  # noqa: BLE001 — 见上：先发完，再抛
        deferred = exc
    if verdict.state == "dirty":
        conflict = self._conflict_nudge(card=card, status=status)
        if conflict is not None:
            pending.append(conflict)
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

    if verdict.state == "clean":
        # CLEAN → 通知验收人（按 head 去重）。
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

    if card.auto_merge_armed_by and verdict.state in ("clean", "unstable"):
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
        )
        return
    await self._session.flush()


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
    # `write`, not `read` — this PUSHES a merge of main onto the PR branch
    # (the read mint has no `contents:write`; PR #575/#582 froze on that).
    updated = await client.update_branch(
        owner=owner, repo=repo, number=card.pr_number or 0, token=creds.write
    )
    outcome = (
        "已自动更新分支，等新一轮 CI。" if updated else "自动更新分支被拒，下一轮重试。"
    )
    card.rebase_count += 1
    notes.annotate(card, f"基线落后于 main，{outcome}")
    await self._session.flush()
