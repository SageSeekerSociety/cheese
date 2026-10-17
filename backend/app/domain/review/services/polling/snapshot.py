"""The read path's own guarantee that a card's merge snapshot is fresh, and the
GitHub-side recomputation behind it."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.core.errors import ValidationError
from app.domain.review import notes
from app.domain.review import services as pkg
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.review.services._shared import logger
from app.domain.topic.models import Topic, TopicStatus

#: The refresh under way for each card. A room's card list is read by every
#: open page of it every 15 s; they share one round of GitHub calls per card.
_refreshing: dict[uuid.UUID, asyncio.Task[None]] = {}


async def refresh_stale_pr_snapshots(
    self: pkg.AcceptService, cards: Sequence[AcceptCard]
) -> None:
    """读卡这条路，把卡面上过期的合并态补上。

    卡上的 `merge_state` 是一份**快照**，而前端那颗「采纳」按钮按它亮不亮
    (`TopicAcceptCard.vue` 的 `acceptBlockedTitle`)。快照平时只有两个时候
    重算：轮询器 (`accept_pr_poll_interval_s`，默认 60s)，和点下采纳的那一
    刻。夹在两者之间来读的人只会读到陈旧的一份 —— 实测 CI 在 11:28Z 就全绿
    了，卡到 11:49Z 才写进 `clean`，中间二十来分钟界面上是「检查全绿、按钮
    点不动」；更短的一次是 12:06Z 卡上被写成 `unknown`（GitHub 那一刻还没算
    完 mergeable），而此刻 `/pr-checks` 早已 `mergeable: true`，快照又冻了一个
    轮询周期（那会儿是 300s）。界面每 15s 来读一次，读到的却是同一份旧快照，
    **靠前端轮询自己收敛不了**。

    所以给读这条路一个它自己的保证：快照比 `accept_pr_snapshot_floor_s` 还
    旧，就重算一次。地板是必须的 —— 读卡是热点，不能每个读者都触发一次外呼。

    只做一件事：把当前判定重算一遍写回去（具体交给托管方那一侧的能力，
    `Forge.refresh_snapshot`）。轮询器做的另外两件**不做** —— 不发事件、
    不合并 (那是它的职责，`advance_pr_card` 的 docstring)。任何失败都吞掉：
    读卡不能因为 GitHub 抖一下就 500，快照下一轮重试就是。

    过期的几张卡并排重算，每张各用一个会话；同一张卡同一时刻只算一份，几个
    读者一起等它。这次读最多等 `accept_pr_snapshot_read_wait_s`：没算完的接着
    在后台算完、写回去，下一次读就是新的。一张接一张地等过 GitHub，一次读卡
    曾经要 40 秒。
    """
    stale = [card for card in cards if _snapshot_is_due(self, card)]
    if not stale:
        return
    sessions = async_sessionmaker(self._session.bind, expire_on_commit=False)
    running = {card.id: _refresh_once(sessions, card.id) for card in stale}
    done, _ = await asyncio.wait(
        running.values(), timeout=settings.accept_pr_snapshot_read_wait_s
    )
    for card in stale:
        if running[card.id] in done:
            await self._session.refresh(card)


def _snapshot_is_due(self: pkg.AcceptService, card: AcceptCard) -> bool:
    if card.status != AcceptStatus.pending or card.pr_number is None:
        return False
    age = self._merge_snapshot_age_s(card)
    return age is None or age >= settings.accept_pr_snapshot_floor_s


def _refresh_once(
    sessions: async_sessionmaker, card_id: uuid.UUID
) -> asyncio.Task[None]:
    task = _refreshing.get(card_id)
    if task is None:
        task = asyncio.get_running_loop().create_task(
            _refresh_in_own_session(sessions, card_id),
            name=f"refresh merge snapshot {card_id}",
        )
        _refreshing[card_id] = task
    return task


async def _refresh_in_own_session(
    sessions: async_sessionmaker, card_id: uuid.UUID
) -> None:
    """One card's refresh on a session of its own: the read that started it may
    have answered and closed its own by the time GitHub does."""
    try:
        async with sessions() as session:
            svc = pkg.AcceptService(session)
            card = await svc._repo.get(card_id)
            if card is not None:
                await svc._refresh_stale_pr_snapshot(card)
                await session.commit()
    except Exception:  # noqa: BLE001 — a read never fails over a poll
        logger.warning(
            "card %s: refreshing a stale merge snapshot failed", card_id, exc_info=True
        )
    finally:
        _refreshing.pop(card_id, None)


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
    if not _snapshot_is_due(self, card):
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
    if card.note_code == notes.NoteCode.waiting_merge_queue:
        # 队列里的卡不在这里重算。GitHub 把它的 `mergeStateStatus` 钉在 BLOCKED
        # 上，从 PR 自己的信号重算只会得到「GitHub 合并状态：blocked」——队列
        # 自己那套检查它看不见，于是重算出来的那份会把轮询写下的「还在等 CI」
        # 抹掉（`reasons` 里少了 `ci_running`），而环境释放判据读的正是那句话：
        # 一张还在队里等的卡就此被当成没人再看，机器被收掉。这张卡的镜像归
        # `_poll_pr_card` 的队列分支管，它每跳问一次 GitHub 队列条目还在不在。
        return
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
