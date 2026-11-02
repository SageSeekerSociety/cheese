"""Accept card data access."""

import uuid
from collections.abc import Collection, Iterable
from datetime import datetime

from sqlalchemy import (
    Row,
    Uuid,
    and_,
    any_,
    bindparam,
    delete,
    func,
    inspect,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_facts import recall, remember
from app.domain.project.models import ProjectForge
from app.domain.review.models import (
    AcceptApproval,
    AcceptCard,
    AcceptCardReviewer,
    AcceptStatus,
    DeliverableKind,
)
from app.domain.room_task.models import Task, TaskStatus
from app.domain.room_task.presentation import card_waits_on_reviewer
from app.domain.topic.models import Topic, TopicStatus

_CARD_REVIEWER_TABLES = frozenset({"accept_card_reviewer", "accept_cards"})


def _can_cache(session: object) -> bool:
    return isinstance(session, AsyncSession)


def _clean_handles(handles: Iterable[str]) -> list[str]:
    """去空白、去重、保序 —— 一条集合语义的边（空 handle 不是评审人）。"""
    seen: dict[str, None] = {}
    for handle in handles:
        name = (handle or "").strip()
        if name:
            seen.setdefault(name, None)
    return list(seen)


class AcceptCardRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(
        self,
        *,
        topic_id: uuid.UUID,
        reviewer_handles: Iterable[str],
        focus: str = "",
        status: AcceptStatus = AcceptStatus.pending,
        change_subject: str | None = None,
        change_body: str | None = None,
        task_id: uuid.UUID | None = None,
        delivered_task_ids: list[uuid.UUID] | None = None,
        artifact_id: uuid.UUID | None = None,
        deliverable_kind: DeliverableKind | None = None,
        deliverable_name: str | None = None,
        deliverable_url: str | None = None,
        completes_task: bool = True,
    ) -> AcceptCard:
        # 递卡是房间的事 —— 一棵树 = 一个分支 = 一个 PR = 一批活, and the batch
        # belongs to the room, not to any one card in it.
        card = AcceptCard(
            topic_id=topic_id,
            task_id=task_id,
            focus=focus,
            status=status,
            change_subject=change_subject,
            change_body=change_body,
            delivered_task_ids=[str(t) for t in (delivered_task_ids or [])],
            artifact_id=artifact_id,
            deliverable_kind=deliverable_kind,
            deliverable_name=deliverable_name,
            deliverable_url=deliverable_url,
            completes_task=completes_task,
        )
        self._session.add(card)
        await self._session.flush()
        await self._session.refresh(card)
        await self.set_reviewers(card.id, reviewer_handles)
        return card

    async def reviewer_handles(self, card_id: uuid.UUID) -> list[str]:
        """谁被派来评审这张卡。

        一卡多人的说法在 `accept_card_reviewer` 这一张表：一卡多行，按派单先后
        回来。表里一行都没有时回落读 `accept_cards.reviewer_handle` 那一列 ——
        那是这一刀之前建的卡，也从迁移 `a2f7c1e9d4b3` 回填了；直接拿 ORM 建卡
        的地方（测试的夹具）只写这一列。回落只在这一种情况下发生：表里有行，
        以表为准，删光就是删光。
        """
        if _can_cache(self._session):
            return (await self.reviewer_handles_for_cards([card_id])).get(card_id, [])
        stmt = (
            select(AcceptCardReviewer.handle)
            .where(AcceptCardReviewer.card_id == card_id)
            .order_by(AcceptCardReviewer.created_at)
        )
        handles = list((await self._session.scalars(stmt)).all())
        if handles:
            return handles
        card = await self._session.get(AcceptCard, card_id)
        single = getattr(card, "reviewer_handle", None) if card is not None else None
        return [single] if single else []

    async def reviewer_handles_for_cards(
        self, card_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[str]]:
        """一批卡的评审人，一次查完 —— 侧栏/快照一次画很多张卡时用。

        按 created_at 升序回来，所以同一张卡后写的排在后面；一卡可以有多行。
        """
        if not card_ids:
            return {}
        if _can_cache(self._session):
            if self._session.autoflush:
                await self._session.flush()
            out: dict[uuid.UUID, list[str]] = {}
            unseen: list[uuid.UUID] = []
            for cid in dict.fromkeys(card_ids):
                hit, cached = recall(self._session, ("card_reviewer_handles", cid))
                if hit:
                    if cached:
                        out[cid] = list(cached)
                else:
                    unseen.append(cid)
            if not unseen:
                return out
            stmt = (
                select(AcceptCardReviewer.card_id, AcceptCardReviewer.handle)
                .where(AcceptCardReviewer.card_id.in_(unseen))
                .order_by(AcceptCardReviewer.created_at)
            )
            found: dict[uuid.UUID, list[str]] = {}
            for card_id, handle in (await self._session.execute(stmt)).all():
                found.setdefault(card_id, []).append(handle)
            missing = [cid for cid in unseen if cid not in found]
            if missing:
                identity_map = self._session.sync_session.identity_map
                still_missing: list[uuid.UUID] = []
                for cid in missing:
                    in_map = identity_map.get(
                        self._session.sync_session.identity_key(AcceptCard, (cid,))
                    )
                    if in_map is not None and not inspect(in_map).expired_attributes:
                        if in_map.reviewer_handle:
                            found[cid] = [in_map.reviewer_handle]
                    else:
                        still_missing.append(cid)
                if still_missing:
                    cards = await self._session.scalars(
                        select(AcceptCard).where(AcceptCard.id.in_(still_missing))
                    )
                    for card in cards:
                        if card.reviewer_handle:
                            found[card.id] = [card.reviewer_handle]
            for cid in unseen:
                handles = found.get(cid, [])
                remember(
                    self._session,
                    ("card_reviewer_handles", cid),
                    tuple(handles),
                    tables=_CARD_REVIEWER_TABLES,
                )
                if handles:
                    out[cid] = handles
            return out
        stmt = (
            select(AcceptCardReviewer.card_id, AcceptCardReviewer.handle)
            .where(AcceptCardReviewer.card_id.in_(card_ids))
            .order_by(AcceptCardReviewer.created_at)
        )
        found = {}
        for card_id, handle in (await self._session.execute(stmt)).all():
            found.setdefault(card_id, []).append(handle)
        # 表里没有行的那些卡（这一刀之前建的、或夹具直接写列建的）读影子列，
        # 与 `reviewer_handles` 同一条回落。
        missing = [cid for cid in card_ids if cid not in found]
        if missing:
            cards = await self._session.scalars(
                select(AcceptCard).where(AcceptCard.id.in_(missing))
            )
            for card in cards:
                if card.reviewer_handle:
                    found[card.id] = [card.reviewer_handle]
        return found

    async def set_reviewers(self, card_id: uuid.UUID, handles: Iterable[str]) -> None:
        """把这张卡的评审人集合替换成 `handles` —— 删掉这张卡的全部旧行，再按集合
        插回去（去重、丢空白）。

        一卡多人：集合可以不止一个人；任一评审人采纳即可推进（`accept` 那一侧只
        问「在不在这个集合里」）。
        """
        names = _clean_handles(handles)
        await self._session.execute(
            delete(AcceptCardReviewer).where(AcceptCardReviewer.card_id == card_id)
        )
        for name in names:
            self._session.add(AcceptCardReviewer(card_id=card_id, handle=name))
        await self._session.flush()

    async def list_approver_handles(self, card_id: uuid.UUID) -> list[str]:
        stmt = (
            select(AcceptApproval.approver_handle)
            .where(AcceptApproval.card_id == card_id)
            .order_by(AcceptApproval.created_at)
        )
        return list((await self._session.scalars(stmt)).all())

    async def approver_handles_by_card(
        self, card_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[str]]:
        """`list_approver_handles` for several cards in one read."""
        found: dict[uuid.UUID, list[str]] = {}
        if not card_ids:
            return found
        rows = await self._session.execute(
            select(AcceptApproval.card_id, AcceptApproval.approver_handle)
            .where(
                AcceptApproval.card_id
                == any_(bindparam(None, card_ids, type_=ARRAY(Uuid)))
            )
            .order_by(AcceptApproval.created_at)
        )
        for card_id, handle in rows:
            found.setdefault(card_id, []).append(handle)
        return found

    async def add_approval(self, card_id: uuid.UUID, approver_handle: str) -> None:
        """Record a vote; idempotent — (card, approver) is unique by design."""
        if approver_handle in await self.list_approver_handles(card_id):
            return
        self._session.add(
            AcceptApproval(card_id=card_id, approver_handle=approver_handle)
        )
        await self._session.flush()

    async def clear_approvals(self, card_id: uuid.UUID) -> None:
        """新提交作废已有的采纳 (#718, dismiss_stale): drop every vote this
        card has collected — they were cast on a head that no longer exists."""
        await self._session.execute(
            delete(AcceptApproval).where(AcceptApproval.card_id == card_id)
        )
        await self._session.flush()

    async def get(self, card_id: uuid.UUID) -> AcceptCard | None:
        return await self._session.get(AcceptCard, card_id)

    async def list_for_task(self, task_id: uuid.UUID) -> list[AcceptCard]:
        """Every card that has ever delivered this tree, newest first.

        The scope "one card at a time" is really about: a tree has one branch
        and therefore one PR, and two live cards on it would be two PRs racing
        each other on the same commits.
        """
        if _can_cache(self._session):
            if self._session.autoflush:
                await self._session.flush()
            found, cached = recall(self._session, ("task_accept_cards", task_id))
            if found:
                return list(reversed(cached))
        stmt = (
            select(AcceptCard)
            .where(AcceptCard.task_id == task_id)
            .order_by(AcceptCard.created_at.desc())
        )
        rows = list((await self._session.scalars(stmt)).all())
        if _can_cache(self._session):
            remember(
                self._session,
                ("task_accept_cards", task_id),
                tuple(reversed(rows)),
                tables=frozenset({"accept_cards"}),
            )
        return rows

    async def list_for_topic(self, topic_id: uuid.UUID) -> list[AcceptCard]:
        """Every card filed under this room: its tasks' cards, each carrying
        its `task_id`. A caller asking about one task filters on that."""
        stmt = (
            select(AcceptCard)
            .where(
                AcceptCard.topic_id == topic_id,
            )
            .order_by(AcceptCard.created_at.desc())
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_everywhere_in_room(self, topic_id: uuid.UUID) -> list[AcceptCard]:
        """Every card filed anywhere in this room — its own and its cards'.

        The room's own set (`list_for_topic`) is the answer to "do I have a
        card". This is the answer to "what is still open in here", which is a
        different question and has to include what a piece of work filed back
        when work was a place: an unsettled row riding a PR is one the poller
        keeps following, and archiving the room is exactly when that must stop.
        """
        stmt = (
            select(AcceptCard)
            .where(AcceptCard.topic_id == topic_id)
            .order_by(AcceptCard.created_at.desc())
        )
        return list((await self._session.scalars(stmt)).all())

    async def latest_by_task(
        self, task_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, AcceptCard]:
        """The newest card on each of these threads, in ONE query.

        Batched on purpose. The rail shows every thread with the PR it rides
        on, and asking per thread is the N+1 that turns one sidebar into one
        request per piece of work ever dispatched. Newest wins because a thread
        can file again after a rejection, and the current card is the one that
        says where the work stands.
        """
        return (await self.latest_and_counts_by_task(task_ids))[0]

    async def latest_and_counts_by_task(
        self, task_ids: Collection[uuid.UUID]
    ) -> tuple[dict[uuid.UUID, AcceptCard], dict[uuid.UUID, int]]:
        """`latest_by_task`, plus how many cards each thread has filed in all
        (「第 N 版」 on a card in review). The same rows answer both, so it is
        still one query."""
        if not task_ids:
            return {}, {}
        if _can_cache(self._session):
            if self._session.autoflush:
                await self._session.flush()
            latest: dict[uuid.UUID, AcceptCard] = {}
            counts: dict[uuid.UUID, int] = {}
            missing: list[uuid.UUID] = []
            for tid in dict.fromkeys(task_ids):
                found, cached = recall(self._session, ("task_accept_cards", tid))
                if found:
                    if cached:
                        latest[tid] = cached[-1]
                        counts[tid] = len(cached)
                else:
                    missing.append(tid)
            if not missing:
                return latest, counts
            stmt = (
                select(AcceptCard)
                .where(
                    AcceptCard.task_id
                    == any_(bindparam(None, missing, type_=ARRAY(Uuid)))
                )
                .order_by(AcceptCard.created_at, AcceptCard.id)
            )
            by_task: dict[uuid.UUID, list[AcceptCard]] = {tid: [] for tid in missing}
            for card in (await self._session.scalars(stmt)).all():
                if card.task_id is not None:
                    by_task.setdefault(card.task_id, []).append(card)
            for tid, cards in by_task.items():
                remember(
                    self._session,
                    ("task_accept_cards", tid),
                    tuple(cards),
                    tables=frozenset({"accept_cards"}),
                )
                if cards:
                    latest[tid] = cards[-1]
                    counts[tid] = len(cards)
            return latest, counts
        stmt = (
            select(AcceptCard)
            # 和 `approver_handles_by_card` 同一个写法：「待我处理」拿这里一问就是
            # 几百条活，`in_()` 会把它们摊成同样多个绑定参数、每执行一次重拼一遍
            # SQL；一个数组参数只编译一次，之后每次都由驱动直接复用。
            .where(
                AcceptCard.task_id
                == any_(bindparam(None, list(task_ids), type_=ARRAY(Uuid)))
            )
            .order_by(AcceptCard.created_at, AcceptCard.id)
        )
        latest = {}
        counts = {}
        for card in (await self._session.scalars(stmt)).all():
            # Ordered oldest-first, so the last write per key is the newest.
            if card.task_id is not None:
                latest[card.task_id] = card
                counts[card.task_id] = counts.get(card.task_id, 0) + 1
        return latest, counts

    async def listed(
        self,
        *,
        room_ids: Collection[uuid.UUID],
        task_ids: Collection[uuid.UUID],
        open_statuses: tuple[AcceptStatus, ...],
    ) -> list[Row]:
        """The columns a list reads of the cards it shows, in ONE query: every
        card on these tasks, and the undecided cards these rooms filed for
        **themselves** (`task_id` NULL), oldest first.

        Two questions on one table, asked together: a list shows what each of
        its tasks rides on (the newest card wins, as in `latest_by_task`) and
        whether a room's own card waits on someone (open statuses only). Asked
        apart they read the table twice and bring back every open card of every
        task in the room only to throw it away. Columns, not entities: the
        caller folds them into `queries.RailCard`, a value with no session.
        """
        rooms, tasks = list(room_ids), list(task_ids)
        if not rooms and not tasks:
            return []
        # 两串 id 各收成一个数组参数：「待我处理」一问就是几百条活，`in_()` 会把它们
        # 摊成同样多个绑定参数、每执行一次重拼一遍 SQL。
        in_rooms = any_(bindparam(None, rooms, type_=ARRAY(Uuid)))
        on_tasks = any_(bindparam(None, tasks, type_=ARRAY(Uuid)))
        stmt = (
            select(
                AcceptCard.id,
                AcceptCard.topic_id,
                AcceptCard.task_id,
                AcceptCard.status,
                AcceptCard.note_code,
                AcceptCard.merge_state,
                AcceptCard.decided_by,
                AcceptCard.auto_merge_armed_by,
                AcceptCard.pr_number,
                AcceptCard.pr_url,
                AcceptCard.change_subject,
                AcceptCard.note,
                AcceptCard.deliverable_name,
                AcceptCard.created_at,
            )
            .where(
                or_(
                    and_(
                        AcceptCard.topic_id == in_rooms,
                        AcceptCard.task_id.is_(None),
                        AcceptCard.status.in_(open_statuses),
                    ),
                    AcceptCard.task_id == on_tasks,
                )
            )
            .order_by(AcceptCard.created_at, AcceptCard.id)
        )
        return list((await self._session.execute(stmt)).all())

    async def list_recent_for_places(
        self,
        place_ids: list[uuid.UUID],
        *,
        open_statuses: tuple[AcceptStatus, ...],
        settled_since: datetime,
    ) -> list[AcceptCard]:
        """这些地方（房间或活）上的卡：还没结算的全部，加上 `settled_since` 之后递
        的已结算的 —— 按 created_at 升序，同一个地方后递的覆盖先递的就是「最新那张」。

        侧栏要问「这个地方最新那张卡停在哪」：只拉没结算的，就分不出「退回了还没重
        递」和「早就合了」；拉全部历史又太多。结算的只看最近一段就够。

        **关掉的活不算。** 卡描述的是「此刻还有东西在走」；一条已经关掉的活名下的
        卡，哪怕没结算，也不再说明任何事。不在这里挡住，侧栏会拿它算出「名下的活
        在交付链路上」，让一个 10-12 就关掉的活到今天还让房间亮绿灯。房间自己的卡
        （`task_id` 空）不受影响 —— 它挂的不是活。
        """
        if not place_ids:
            return []
        stmt = (
            select(AcceptCard)
            .outerjoin(Task, Task.id == AcceptCard.task_id)
            .where(
                or_(
                    AcceptCard.topic_id.in_(place_ids),
                    AcceptCard.task_id.in_(place_ids),
                ),
                or_(
                    AcceptCard.status.in_(open_statuses),
                    AcceptCard.created_at >= settled_since,
                ),
                or_(AcceptCard.task_id.is_(None), Task.status == TaskStatus.open),
            )
            .order_by(AcceptCard.created_at)
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_open_on_closed_tasks(
        self,
        open_statuses: tuple[AcceptStatus, ...],
        *,
        project_id: uuid.UUID | None = None,
    ) -> list[AcceptCard]:
        """已关的活上还没结算的卡 —— 关任务时漏下、或修复之前就停在那里的那些。

        轮询器拿它做第二道锁（`review.archive.close_cards_for_closed_tasks`）：
        这些卡不该再被跟进，行的状态也该跟着活一起收敛。
        """
        stmt = (
            select(AcceptCard)
            .join(Task, Task.id == AcceptCard.task_id)
            .join(Topic, Topic.id == AcceptCard.topic_id)
            .where(
                Task.status == TaskStatus.closed,
                AcceptCard.status.in_(open_statuses),
            )
            .order_by(AcceptCard.created_at)
        )
        if project_id is not None:
            stmt = stmt.where(Topic.project_id == project_id)
        return list((await self._session.scalars(stmt)).all())

    async def held_pr_deliveries(
        self, project_id: uuid.UUID, open_statuses: tuple[AcceptStatus, ...]
    ) -> list[tuple[int, str | None]]:
        """这个项目里还在等人决定的卡骑着的 PR：号码，和它的分支（任务的分支）。"""
        stmt = (
            select(AcceptCard.pr_number, Task.branch_name)
            .join(Task, Task.id == AcceptCard.task_id)
            .join(Topic, Topic.id == AcceptCard.topic_id)
            .where(
                Topic.project_id == project_id,
                AcceptCard.status.in_(open_statuses),
                AcceptCard.pr_number.is_not(None),
                AcceptCard.pr_merged_at.is_(None),
            )
        )
        return [(row[0], row[1]) for row in (await self._session.execute(stmt)).all()]

    async def returned_since(
        self, task_ids: list[uuid.UUID], since: datetime
    ) -> list[AcceptCard]:
        """这些任务上 `since` 之后被验收人退回的卡，按退回的先后。"""
        if not task_ids:
            return []
        stmt = (
            select(AcceptCard)
            .where(
                AcceptCard.task_id.in_(task_ids),
                AcceptCard.status == AcceptStatus.rejected,
                AcceptCard.decided_at >= since,
            )
            .order_by(AcceptCard.decided_at)
        )
        return list((await self._session.scalars(stmt)).all())

    async def accepted_for_tasks(self, task_ids: list[uuid.UUID]) -> list[AcceptCard]:
        """这些任务上被采纳的每一次交付，按采纳的先后。

        并列的决定时间上再按 id 排：调用方取最后一张当「最新的那次交付」，而这份
        结果现在会进任务清单的 ETag（`topics_tasks.py`）——同一次查询两次跑出不同
        的顺序就会让清单每次都换 tag，304 永不命中。
        """
        if not task_ids:
            return []
        if _can_cache(self._session):
            if self._session.autoflush:
                await self._session.flush()
            cached_accepted: list[AcceptCard] = []
            missing: list[uuid.UUID] = []
            for tid in dict.fromkeys(task_ids):
                found, cached = recall(self._session, ("task_accept_cards", tid))
                if found:
                    cached_accepted.extend(
                        c for c in cached if c.status == AcceptStatus.accepted
                    )
                else:
                    missing.append(tid)
            if not missing:
                cached_accepted.sort(
                    key=lambda c: (c.decided_at is None, c.decided_at, c.id)
                )
                return cached_accepted
            stmt = (
                select(AcceptCard)
                .where(
                    AcceptCard.task_id.in_(missing),
                    AcceptCard.status == AcceptStatus.accepted,
                )
                .order_by(AcceptCard.decided_at, AcceptCard.id)
            )
            fetched = list((await self._session.scalars(stmt)).all())
            all_accepted = cached_accepted + fetched
            all_accepted.sort(key=lambda c: (c.decided_at is None, c.decided_at, c.id))
            return all_accepted
        stmt = (
            select(AcceptCard)
            .where(
                AcceptCard.task_id.in_(task_ids),
                AcceptCard.status == AcceptStatus.accepted,
            )
            .order_by(AcceptCard.decided_at, AcceptCard.id)
        )
        return list((await self._session.scalars(stmt)).all())

    async def accepted_missing_merge_actor(
        self, *, since: datetime, limit: int
    ) -> list[uuid.UUID]:
        """Cards merged since `since` whose PR merger is still unknown.

        `merged_by` NULL is "nobody asked yet"; `{}` is "asked, GitHub named
        nobody" and is excluded here so the sweep stops spending reads on it.
        Only cards with no platform decider need the GitHub fact — a click on
        the platform already names the person. Only a GitHub binding can
        answer: a Forgejo project's merged PR has no GitHub `merged_by` to
        read, and asking it every tick would never fill the column.
        """
        stmt = (
            select(AcceptCard.id)
            .join(Topic, Topic.id == AcceptCard.topic_id)
            .join(ProjectForge, ProjectForge.project_id == Topic.project_id)
            .where(
                AcceptCard.status == AcceptStatus.accepted,
                AcceptCard.decided_by.is_(None),
                AcceptCard.pr_number.is_not(None),
                AcceptCard.merged_by.is_(None),
                AcceptCard.pr_merged_at.is_not(None),
                AcceptCard.pr_merged_at >= since,
                ProjectForge.kind == "github_app",
            )
            .order_by(AcceptCard.pr_merged_at)
            .limit(limit)
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_live_for_places(
        self, place_ids: list[uuid.UUID], *, statuses: tuple[AcceptStatus, ...]
    ) -> list[AcceptCard]:
        """Undecided cards on ANY of these places — rooms or threads.

        Matched on EITHER key. A card filed from a thread stores the room in
        `topic_id` and the thread in `task_id`, so asking only about `topic_id`
        answers "no card" for every thread — and the caller is
        `anybody_still_waiting`, whose "no" closes the place and revokes the very
        card the reviewer had not seen yet. That is the 2026-08-16 incident this
        whole guard was written for, one shape over.
        """
        if not place_ids:
            return []
        # 同一个数组参数在两把键上各用一次：见 `latest_by_task`，一串 id 摊成几百
        # 个绑定参数是「待我处理」那类跨项目清单里最贵的一笔。
        among = any_(bindparam(None, list(place_ids), type_=ARRAY(Uuid)))
        stmt = (
            select(AcceptCard)
            .where(
                or_(AcceptCard.topic_id == among, AcceptCard.task_id == among),
                AcceptCard.status.in_(statuses),
            )
            .order_by(AcceptCard.created_at)
        )
        return list((await self._session.scalars(stmt)).all())

    async def reviewer_topic_ids(
        self, topic_ids: list[uuid.UUID], reviewer_handle: str
    ) -> dict[uuid.UUID, bool]:
        """{topic_id: is one of its cards still waiting on this reviewer} for
        every topic here that ever routed a card to them, in ONE query.

        Two facts in one row because they come from the same scan and the
        sidebar needs both: *being named* on a card is a lasting relationship
        with the topic (it stays yours after you accept it), while *pending* is
        the transient "this is on your desk right now". `pending` alone is the
        waiting state — a card in `pending_gate`/`gate_failed`/`conflict` is
        with 芝士, and an `accepted` one has already been decided.

        关掉的活上的卡不算「在等你」：那件事已经结束，一件 10-12 就关掉的活
        不该让人在 10-17 还收到一盏黄灯。和侧栏其他条判据同一个限制
        （`list_recent_for_places`）。
        """
        if not topic_ids:
            return {}

        stmt = (
            select(AcceptCard)
            .join(AcceptCardReviewer, AcceptCardReviewer.card_id == AcceptCard.id)
            .outerjoin(Task, Task.id == AcceptCard.task_id)
            .where(
                AcceptCard.topic_id.in_(topic_ids),
                AcceptCardReviewer.handle == reviewer_handle,
                or_(AcceptCard.task_id.is_(None), Task.status == TaskStatus.open),
            )
        )
        named: dict[uuid.UUID, bool] = {}
        for card in (await self._session.scalars(stmt)).all():
            # 「在等他」和看板「待处理」那一格同一个判据：一张 pending 的卡如果
            # CI 挂了、平台还在换基、状态还没看过，采纳按钮本来就点不了，下一步
            # 在芝士/平台手上，不算他的事。
            waiting = card_waits_on_reviewer(card)
            named[card.topic_id] = named.get(card.topic_id, False) or waiting
        return named

    async def latest_decision_at(self, place_ids: list[uuid.UUID]) -> datetime | None:
        """When a card on these places last changed hands — NULL if there are no
        cards at all.

        `decided_at` first, `updated_at` as the fallback: a card condemned by
        the gate never gets a `decided_at` (nobody decided it), yet its moment
        is exactly what a "give them a window to re-file" clock has to start
        from.
        """
        if not place_ids:
            return None
        stmt = select(
            func.max(func.coalesce(AcceptCard.decided_at, AcceptCard.updated_at))
        ).where(
            or_(
                AcceptCard.topic_id.in_(place_ids),
                AcceptCard.task_id.in_(place_ids),
            )
        )
        return (await self._session.scalars(stmt)).first()

    async def list_stale_pending_gate(self, cutoff: datetime) -> list[AcceptCard]:
        """孤儿卡扫底 (2026-08-11): cards still waiting on a gate that started
        (or, failing that, was filed) before ``cutoff``.

        The clock is `COALESCE(gate_started_at, created_at)`, not `created_at`:
        a long worktree preparation legitimately delays the check, and rows
        written before `gate_started_at` existed have no start time at all — the
        COALESCE keeps both aging out without ever ageing a card out EARLY.

        Archived topics are excluded because `review/archive.py` already closed
        their cards; anything left there is not a deadlock (that topic can't be
        re-递卡'd anyway) and re-condemning it would just spam its history.
        """
        stmt = (
            select(AcceptCard)
            .join(Topic, Topic.id == AcceptCard.topic_id)
            .where(
                AcceptCard.status == AcceptStatus.pending_gate,
                Topic.status != TopicStatus.archived,
                func.coalesce(AcceptCard.gate_started_at, AcceptCard.created_at)
                < cutoff,
            )
            .order_by(AcceptCard.created_at)
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_live_in_project(
        self, project_id: uuid.UUID, *, statuses: tuple[AcceptStatus, ...]
    ) -> list[AcceptCard]:
        """Every undecided card anywhere in a project, with its topic.

        Scoped by project rather than by topic because the question it answers
        is about *siblings*: two rooms in the same project each about to land a
        change (#314). Archived topics are excluded — their cards are already
        closed, and a room nobody is tracking cannot collide with anything.
        """
        stmt = (
            select(AcceptCard)
            .join(Topic, Topic.id == AcceptCard.topic_id)
            .where(
                Topic.project_id == project_id,
                Topic.status != TopicStatus.archived,
                AcceptCard.status.in_(statuses),
            )
            .order_by(AcceptCard.created_at)
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_awaiting_merge_on_active_topics(
        self, project_id: uuid.UUID | None = None
    ) -> list[AcceptCard]:
        """Pending PRs and returned batches that can still merge externally.

        孤儿卡修复 (2026-08-10): the topic's status is part of the predicate, not
        just the card's. Without the join this returned cards on ARCHIVED topics
        too, and the poller kept driving them every 60s with GitHub credentials
        — for work nobody is tracking any more. `TopicService._archive_one`
        closes those cards at archive time; this join is the second lock,
        covering rows that predate the fix or arrive by some future archive
        path.

        一条关掉的活也一样：它名下的卡不再驱动任何东西。这里挡掉它，归档那条
        判据就完整了 —— 房间还在、活已经结束，平台没有理由再拿人的 GitHub
        凭据去跟进它（`review.archive.close_cards_for_closed_tasks` 负责把行
        也收敛掉）。
        """
        task_open = or_(AcceptCard.task_id.is_(None), Task.status == TaskStatus.open)
        stmt = (
            select(AcceptCard)
            .join(Topic, Topic.id == AcceptCard.topic_id)
            .outerjoin(Task, Task.id == AcceptCard.task_id)
            .where(
                or_(
                    and_(
                        AcceptCard.status == AcceptStatus.pending,
                        task_open,
                    ),
                    and_(
                        AcceptCard.status == AcceptStatus.rejected,
                        AcceptCard.pr_merged_at.is_(None),
                        Task.status == TaskStatus.open,
                    ),
                ),
                AcceptCard.pr_number.is_not(None),
                Topic.status != TopicStatus.archived,
            )
            .order_by(
                (AcceptCard.status == AcceptStatus.pending).desc(),
                AcceptCard.created_at.desc(),
            )
        )
        if project_id is not None:
            stmt = stmt.where(Topic.project_id == project_id)
        cards = []
        seen_trees: set[uuid.UUID] = set()
        for card in (await self._session.scalars(stmt)).all():
            # A resubmitted batch belongs to its pending card; otherwise use
            # its latest return instead of polling every historical review.
            if card.task_id is not None:
                if card.task_id in seen_trees:
                    continue
                seen_trees.add(card.task_id)
            cards.append(card)
        return cards
