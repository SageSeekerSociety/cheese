"""The seen-head guard: the head a person saw must be the head that gets merged,
at every entry point that merges one."""

from __future__ import annotations

from typing import NoReturn

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.errors import ValidationError
from app.domain.project.protection import branch_protection_of
from app.domain.review import notes
from app.domain.review import services as pkg
from app.domain.review.models import AcceptCard
from app.domain.review.repositories import AcceptCardRepository
from app.domain.review.services._shared import (
    ProjectRepository,
    _never_shown_message,
    _stale_view_message,
    logger,
)
from app.domain.topic.models import Topic


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
        except Exception as exc:  # refuse anyway; refresh with what we have
            logger.warning("card %s: first-look head read failed: %s", card.id, exc)
    await self._refresh_stale_card(
        card,
        topic,
        live_head=live,
        action=action,
        headline=(f"PR #{number} 还没有显示过任何版本，已刷新到最新版本"),
    )
    raise ValidationError(_never_shown_message(number, action))


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
    except Exception:  # the raise this accompanies must fire
        logger.exception("could not refresh stale card %s", card_id)
