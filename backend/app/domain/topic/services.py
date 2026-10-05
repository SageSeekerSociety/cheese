"""Topic business logic — rooms, and the work dispatched inside them.

A block can be upgraded into a place of its own (讨论升级), a room can dispatch
a piece of work as a thread (从上往下拆解), and a thread's conclusion flows back
to the room it sits in (结论回流).

The tree is one level deep now: the project root has rooms, and rooms have no
topic children at all. What used to be a third level is a `tasks` row — see
`app.domain.room_task.place` for how one id still addresses either.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import overload

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.agent import clone
from app.domain.agent.harness import harness_for
from app.domain.agent_instance.services import (
    AgentInstanceService,
    ResolvedAgent,
)
from app.domain.agent_session.services import AgentSessionService
from app.domain.block.about import EventAbout, landing
from app.domain.block.documents import DocumentWriter
from app.domain.block.models import (
    AuthorType,
    Block,
    BlockKind,
)
from app.domain.block.repositories import BlockRepository
from app.domain.conversation.services import room_of
from app.domain.identity.handles import (
    CHEESE_NAME,
    agent_instance_handle,
    looks_like_agent_handle,
    names_a_person,
)
from app.domain.living_doc.models import Document, DocumentNode
from app.domain.living_doc.services import DocumentJournal, Documents
from app.domain.membership.roster import roster_rows
from app.domain.notification.services import ProjectNotificationService
from app.domain.project.repositories import ProjectRepository
from app.domain.repository import service as ws
from app.domain.review.services import AcceptService
from app.domain.room_task.models import Task, TaskStatus, TaskTitleSource
from app.domain.room_task.place import Place, PlaceResolver
from app.domain.room_task.services import TaskService
from app.domain.topic.doc_change import summarize_doc_change
from app.domain.topic.models import (
    PLACEHOLDER_TITLE,
    RoomCleanup,
    Topic,
    TopicKind,
    TopicRole,
    TopicStatus,
)
from app.domain.topic.overview import (
    ACTIVE_TOPICS_KEY,
    ACTIVE_TOPICS_LIMIT,
    CLOSED_TOPICS_KEY,
    CLOSED_TOPICS_LIMIT,
    first_sentence,
    overview_auto_blocks,
    topic_status,
)
from app.domain.topic.repositories import (
    SortOrder,
    TopicProgressRepository,
    TopicRepository,
    TopicSortField,
)
from app.domain.topic_membership.services import TopicMemberService


@overload
def _as_utc(when: datetime) -> datetime: ...
@overload
def _as_utc(when: None) -> None: ...
def _as_utc(when: datetime | None) -> datetime | None:
    """Read an offset-less instant as UTC, passing None through.

    Two callers need this and used to carry one copy each (which shadowed each
    other — the stricter copy won at runtime and crashed on None): query strings
    routinely arrive as `2026-08-12T00:00:00` with no zone, and some drivers
    hand a TIMESTAMPTZ back naive, which raises rather than merely reading
    wrong when subtracted.
    """
    if when is None or when.tzinfo is not None:
        return when
    return when.replace(tzinfo=UTC)


def _require_room(parent: Topic) -> None:
    """Rooms nest exactly one level: only the root has rooms under it.

    What used to be below a room is not a `topics` row at all any more — it is a
    thread (`dispatch_task`). So there is no longer a kind to derive here, only
    a placement to refuse: a room under a room would be a place with no way to
    end and nothing that ends in it.
    """
    if parent.kind != TopicKind.root:
        raise ValidationError(say("roomUnderRoomUseSplit"))


logger = logging.getLogger("cheesex.topic")


def _brief_doc(
    *, child_title: str, parent_title: str, created_by: str | None, source_block: str
) -> str:
    """A room upgraded out of a private chat starts its living doc from the
    message it came from, copied verbatim under fixed headings. No semantics
    are derived from prose and nothing speaks as 芝士 (CLAUDE.md red line)."""
    by = f"由 {created_by} " if created_by else ""
    return "\n\n".join(
        [
            f"# {child_title}",
            f"> 从「{parent_title}」的一条消息{by}转来时自动预置",
            "## 来源",
            source_block.strip() or "（空）",
        ]
    )


def _is_mid_turn_block(block: Block) -> bool:
    """Is this block the middle of a turn rather than the end of one?

    A tool action by 芝士 — `meta.tool` is stamped by the 现场 event writer, so
    this asks what the block IS rather than parsing its text. Nothing healthy
    leaves one as a topic's last word: the turn either keeps working (another
    action, a message) or fails into a system event.

    「是不是芝士的」按署名判：事件行的档位只说得出「参与者还是平台」，而一个房间
    里的参与者有好几个。

    摆出来的一份东西（`cheese show`）也是一轮的中间：摆完总要说一句它是什么，一轮
    停在「摆出来」上，和停在一个工具动作上是同一种断法。
    """
    if not looks_like_agent_handle(block.author):
        return False
    if block.kind == BlockKind.artifact:
        return True
    return block.kind == BlockKind.event and bool((block.meta or {}).get("tool"))


def _stall_block_summary(block: Block | None) -> dict | None:
    """What the stall verdict was read off, so a caller can check it by hand
    instead of trusting the boolean."""
    if block is None:
        return None
    return {
        "id": str(block.id),
        "kind": str(block.kind),
        "author_type": str(block.author_type),
        "tool": (block.meta or {}).get("tool"),
        "created_at": _as_utc(block.created_at).isoformat(),
    }


@dataclass(frozen=True)
class TopicRelevance:
    """What a topic is to one particular person (与我的相关性, C2).

    See ``TopicOut.i_participate``/``awaits_me`` for the field-level contract.
    Two booleans instead of one enum so that "am I involved" and "is it on my
    desk" stay separable: the sidebar folds on the first and overrides that
    fold on the second.
    """

    i_participate: bool = False
    awaits_me: bool = False


class TopicService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = TopicRepository(session)
        self._projects = ProjectRepository(session)
        self._blocks = BlockRepository(session)
        self._members = TopicMemberService(session)
        # 与我的相关性 asks three other domains the same question at once, and
        # asks each through its own service — the roster, the accept cards and
        # the @-notifications each decide for themselves what "mine" means.
        self._cards = AcceptService(session)
        self._notifications = ProjectNotificationService(session)

    async def get(self, topic_id: uuid.UUID) -> Topic | None:
        """Return one topic for cross-domain service callers."""
        return await self._repo.get(topic_id)

    async def lock(self, topic_id: uuid.UUID) -> Topic | None:
        """Hold the room's row lock for the rest of the transaction, whatever
        state the room is in: for work that orders itself against the room's
        own, such as putting an archived room's sandbox to sleep."""
        return await self._repo.lock(topic_id)

    async def lock_for_execution(self, topic_id: uuid.UUID) -> Topic:
        topic = await self._repo.lock(topic_id)
        if topic is None:
            raise NotFoundError("Topic not found")
        if topic.status == TopicStatus.archived:
            raise ConflictError(say("roomArchivedUnarchiveFirst"))
        return topic

    async def _starting_agent_handle(self, topic: Topic) -> str:
        """The identity of the agent this room starts with.

        A room does not have an agent; it seats one, and seating needs the
        agent's own identity rather than a name derived from the room. Which
        agent a new room starts with is still the project's choice — replacing
        that choice with explicit seating is the next step, and this is the seam
        it will land on.

        Always an answer: a project is created with its 芝士, so the agent a new
        room starts with is a saved row with an identity of its own.
        """
        from app.domain.identity.handles import agent_instance_handle

        return agent_instance_handle((await self.resolve_agent(topic)).instance_id)

    async def resolve_agent(self, topic: Topic) -> ResolvedAgent:
        """The agent a turn here runs as when nobody was addressed.

        A room does not have an agent; a room's fallback is the project's
        default, and a private 1:1 names its own teammate. Nothing hands a room
        to an agent any more — an agent is seated on the roster, and addressed.
        """
        project = await self._projects.get(topic.project_id)
        if project is None:
            raise NotFoundError("Project not found")
        return await AgentInstanceService(self._session).for_topic(topic, project)

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        title: str | None,
        parent_id: uuid.UUID | None = None,
        created_by: str | None = None,
    ) -> Topic:
        """``title=None`` opens an unnamed room (see `TopicRepository.add`)."""
        project = await self._projects.get(project_id)
        if project is None:
            raise NotFoundError("Project not found")
        # A new work topic lives under the project's root (本体) by default, so the
        # tree nests 本体 > 话题 > 分身 instead of placing topics beside the root.
        if parent_id is None:
            parent_id = project.root_topic_id
        kind = TopicKind.topic
        if parent_id is not None:
            parent = await self._repo.get(parent_id)
            if parent is None:
                raise NotFoundError("Parent topic not found")
            _require_room(parent)
            # 父房间必须是**本项目的**房间：`_require_room` 只问 kind，不问归属，
            # 所以拿别人项目里的根房间当 parent，就能把自己的房间挂进那片树——对方的
            # `/children` 从此列出一个他管不着的房间，而树的形状是他以为只有自己人
            # 的地方。跨项目的父子关系没有第二种解释，按「这个父不存在」回答。
            if parent.project_id != project_id:
                raise NotFoundError("Parent topic not found")
        topic = await self._repo.add(
            project_id=project_id,
            title=title,
            parent_id=parent_id,
            kind=kind,
            created_by=created_by,
        )
        # No branch parent: this path only ever makes ROOMS now, and a room forks
        # the base branch. Binding one to its parent would have made the project
        # root a fork point, which nothing has ever wanted.
        #
        # 群聊房间的地基 (fusion-design §3): seed the roster — creator = owner,
        # 芝士 joins as a member. `created_by` alone is not enough: 芝士 itself
        # creating a topic, or a caller whose token didn't resolve (anonymous),
        # would leave the room OWNERLESS — seed() deliberately skips
        # "cheese"/None as owner — and then nobody can manage its roster, and
        # every sub-topic split beneath it inherits the same emptiness
        # (dispatch_task falls back to the PARENT's owner). Nearly the same
        # ladder as dispatch_task, which has one rung this does not: a split
        # happens DURING somebody's turn, so it can ask who is driving. Creating a
        # topic is a standalone act with nobody to ask.
        await self._members.seed(
            topic.id,
            agent_handle=await self._starting_agent_handle(topic),
            owner_handle=await self._resolve_owner(
                created_by,
                project_id=project_id,
                parent_id=parent_id,
                project_owner=project.owner_handle,
            ),
        )
        return topic

    async def _resolve_owner(
        self,
        created_by: str | None,
        *,
        project_id: uuid.UUID,
        parent_id: uuid.UUID | None,
        project_owner: str | None,
    ) -> str | None:
        """Who owns a newborn topic: the real human who created it, else the
        parent room's owner, else the project's owner, else its team's owner.

        That last rung is not decoration. Measured on the dogfooding project
        2026-08-12, answering 「新话题的拥有者为什么有的有，有的是空的」: the
        project's ``owner_handle`` is NULL and its root topic is ownerless too,
        so every topic 芝士 opened under the root fell through all three rungs
        and came out blank — five active rooms with no one able to manage the
        roster. The ladder was right; its bottom had nothing to stand on. The
        project's team does: its owner is "who is in charge here" already
        recorded, not a new policy invented to fill a hole.

        Returns None only when the team has no owner either — the caller still
        seeds 芝士, and the room stays manageable by any project member.

        "Is the creator 芝士" spans the whole agent handle namespace, not the bare
        ``cheese`` string: each 分身 creates under its own ``cheese-<topic hex>``
        handle, and a string match would make the 分身 the room's owner — the one
        thing this chain exists to prevent (same rule as dispatch_task)."""
        if created_by and not looks_like_agent_handle(created_by):
            return created_by
        if parent_id is not None:
            parent_members, _ = await self._members.list_for_topic(parent_id)
            parent_owner = next(
                (m.member_handle for m in parent_members if m.role == TopicRole.owner),
                None,
            )
            if parent_owner:
                return parent_owner
        return project_owner or await self._team_owner(project_id)

    async def _team_owner(self, project_id: uuid.UUID) -> str | None:
        """The owner of the project's team, as the last rung of the ladder.

        Read lazily — only when the rungs above came up empty — so an ordinary
        topic-create still costs no extra query.
        """
        from app.domain.project.services import ProjectService
        from app.domain.team.models import TeamMemberRole
        from app.domain.team.services import team_service
        from app.domain.user.services import usernames_by_ids

        team_id = await ProjectService(self._session).team_for_project(project_id)
        if team_id is None:
            return None
        owners = [
            r.user_id
            for r in await team_service(self._session).get_team_members(team_id)
            if r.role == TeamMemberRole.OWNER
        ]
        names = await usernames_by_ids(self._session, owners)
        return next((names[uid] for uid in owners if uid in names), None)

    async def get_or_create_private(
        self,
        *,
        project_id: uuid.UUID,
        user_handle: str,
        peer_handle: str | None = None,
        agent_handle: str | None = None,
    ) -> Topic:
        """A 1:1 private chat (spec §1).

        With ``peer_handle`` → a person-to-person DM between the two humans
        (shared by both, so the pair is canonicalized). Without → the member's
        1:1 with the AI teammate named by ``agent_handle``, or with the
        project's default when nobody named one. A teammate sits in its DM
        under its seat handle, the way a person sits under theirs, so a DM is
        two handles either way and stays that teammate's afterwards.
        """
        project = await self._projects.get(project_id)
        if project is None:
            raise NotFoundError("Project not found")
        if peer_handle is not None:
            owner, peer = sorted((user_handle, peer_handle))
            title = f"私聊 · {owner} · {peer}"
        else:
            agents = AgentInstanceService(self._session)
            agent = await agents.for_handle(project, agent_handle)
            await agents.ensure_identity(agent)
            owner, peer = user_handle, agent_instance_handle(agent.id)
            title = f"与{agent.display_name or CHEESE_NAME}私聊 · {owner}"
        topic = await self._repo.get_or_create_private(
            project_id=project_id, owner=owner, peer=peer, title=title
        )
        await TopicMemberService(self._session).seed_private(
            topic.id, owner_handle=owner, peer_handle=peer
        )
        return topic

    async def get_or_404(self, topic_id: uuid.UUID) -> Topic:
        topic = await self._repo.get(topic_id)
        if topic is None:
            raise NotFoundError("Topic not found")
        return topic

    async def list_for_project(
        self,
        project_id: uuid.UUID,
        *,
        sort: TopicSortField | None = None,
        order: SortOrder = "asc",
        active_since: datetime | None = None,
    ) -> tuple[list[Topic], dict[uuid.UUID, datetime], int]:
        """The project's topics, their 最后活动时间, and the total.

        Activity comes back with the rows because the query that ordered them
        already derived it; fetching it separately made the database compute
        the same correlated subquery over the same topics twice.
        """
        rows = await self._repo.list_for_project_with_activity(
            project_id,
            sort=sort,
            order=order,
            active_since=_as_utc(active_since),
        )
        topics = [topic for topic, _ in rows]
        last_activity = {topic.id: last for topic, last in rows}
        # `total` counts what the caller got: a filtered page whose total still
        # said "all topics" would tell a paging client to keep asking for rows
        # that do not exist.
        total = (
            len(topics)
            if active_since is not None
            else await self._repo.count_for_project(project_id)
        )
        return topics, last_activity, total

    async def last_activity_for_topics(
        self, topic_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, datetime]:
        return await self._repo.last_activity_for_topics(topic_ids)

    async def relevance_for_topics(
        self, topics: list[Topic], viewer_handle: str | None
    ) -> dict[uuid.UUID, TopicRelevance]:
        """{topic_id: 与我的相关性} for a batch of topics (C2).

        FOUR queries, whatever the batch size — one per way of being involved
        that lives in another table (roster, accept cards, @-notifications,
        decision requests).
        Creation is the fourth way and costs nothing: ``created_by`` is already
        on the rows the caller handed in. The list endpoint returns a whole
        project at once, so a per-topic probe here would be a hundred round
        trips to render a sidebar.

        ``viewer_handle`` is passed IN rather than read from an auth context:
        the caller resolved the actor at the trust boundary, and a service that
        went looking for the request's identity would be unusable from anywhere
        that has no request (the digest builders, tests, 分身 turns).
        Anonymous/unknown callers relate to nothing — every topic comes back
        with both booleans false, which is also the pre-C2 behaviour.
        """
        if viewer_handle is None or not topics:
            return {}
        topic_ids = [t.id for t in topics]
        roster = await self._members.topic_ids_for_member(topic_ids, viewer_handle)
        cards = await self._cards.reviewer_topic_ids(topic_ids, viewer_handle)
        mentions = await self._notifications.mention_topic_ids(topic_ids, viewer_handle)
        decisions = await self._notifications.decision_topic_ids(
            topic_ids, viewer_handle
        )
        relevance: dict[uuid.UUID, TopicRelevance] = {}
        for topic in topics:
            # 「在等我」只数要我**动手拍板**的：一张点名我还没结的验收卡，或一条
            # 还没答的决策请求。未读的 @ 不算——芝士汇报、递卡都会 @人，把它算进
            # 来侧栏几乎每一行都亮橙灯，灯就没有意义了；未读有右边的数字管。
            awaits = cards.get(topic.id, False) or decisions.get(topic.id, False)
            participates = (
                topic.id in roster
                or topic.created_by == viewer_handle
                or topic.id in cards
                or topic.id in mentions
                or topic.id in decisions
            )
            relevance[topic.id] = TopicRelevance(
                # Being awaited is a way of being involved, so it implies
                # participation: a card can be routed to someone who never
                # opened the topic, and the whole point of `awaits_me` is that
                # it must not end up folded away under 「其他话题」.
                i_participate=participates or awaits,
                awaits_me=awaits,
            )
        return relevance

    async def list_children(self, topic_id: uuid.UUID) -> list[Topic]:
        await self.get_or_404(topic_id)
        return await self._repo.list_children(topic_id)

    # ---- 轮次猝死信号 (a turn can die without the process dying) ----------

    async def stall_signal(
        self,
        topic_id: uuid.UUID,
        *,
        live_turn: dict | None,
        threshold_s: float | None = None,
    ) -> dict:
        """Is this topic sitting on a turn that died? A queryable verdict.

        The 8-hour incident (2026-08-11) was not that a turn died — turns die,
        containers get recreated, children get OOM-killed. It was that nothing
        on the platform could be ASKED about it: `status` still read `active`,
        the newest block was an ordinary tool action, and the only way to find
        out was for a human to notice the silence. The orphan sweep now cleans
        such turns up, but a sweep is a background actor: it tells you when it
        acts, not when you ask.

        The verdict rests on two facts that a long-running turn cannot both
        fail, which is what keeps it from crying wolf over slow work:

        1. **No heartbeat.** `live_turn` is what the runner is really executing
           for this topic (frames, tool calls included — activity the DB never
           sees). Present and recently framed ⇒ alive, full stop.
        2. **The timeline stops mid-action.** The newest block is a tool action
           by 芝士 — the shape a turn leaves when it is cut off between doing
           something and reporting it. A turn that ends properly leaves a
           message, and every failure path the platform knows about leaves a
           system event; neither counts as stalled, because in both cases the
           topic already says what happened.
        """
        place = await self.place_or_404(topic_id)
        if threshold_s is None:
            threshold_s = settings.turn_stall_signal_s
        heartbeat_s = None if live_turn is None else live_turn.get("silent_for_s")
        alive = live_turn is not None and (
            heartbeat_s is not None and heartbeat_s <= threshold_s
        )
        # The room's OWN line: a task's conversation is its own, and a room
        # that has gone quiet while one of its tasks talks is still quiet.
        last = await self._blocks.latest_for_topic(place.room_id)
        silent_for_s = (
            None
            if last is None
            else round((datetime.now(UTC) - _as_utc(last.created_at)).total_seconds())
        )
        signal: dict = {
            "stalled": False,
            "reason": None,
            "member": None,
            "threshold_s": round(threshold_s),
            "silent_for_s": silent_for_s,
            "last_block": _stall_block_summary(last),
            "live_turn": live_turn,
        }
        if alive:
            return signal
        if last is None or silent_for_s is None or silent_for_s <= threshold_s:
            return signal
        if not _is_mid_turn_block(last):
            return signal
        signal["stalled"] = True
        # Whose turn died: the member who wrote that last half-finished action.
        signal["member"] = last.author
        # Which of the two ways it died, because they send whoever reads this to
        # different places: a process that is not running the turn at all versus
        # one holding a task that stopped producing.
        signal["reason"] = "no_live_turn" if live_turn is None else "silent_turn"
        return signal

    # ---- 话题级未读 (Feishu-style badges) -------------------------------

    async def unread_counts(
        self, project_id: uuid.UUID, user_handle: str
    ) -> dict[uuid.UUID, int]:
        if await self._projects.get(project_id) is None:
            raise NotFoundError("Project not found")
        return await self._repo.unread_counts(project_id, user_handle)

    async def private_unread_counts(
        self, project_id: uuid.UUID, user_handle: str
    ) -> dict[str, int]:
        if await self._projects.get(project_id) is None:
            raise NotFoundError("Project not found")
        return await self._repo.private_unread_counts(project_id, user_handle)

    async def mark_read(self, conversation_id: uuid.UUID, user_handle: str) -> None:
        """Bump the cursor on a room's own conversation or on one of its tasks."""
        await self.get_or_404(await room_of(self._session, conversation_id))
        await self._repo.mark_read(conversation_id, user_handle)

    async def mark_all_read(
        self, project_id: uuid.UUID, user_handle: str
    ) -> list[uuid.UUID]:
        """「全部标为已读」: bump the cursor on every room of the project that has
        unread messages for this user — the same rooms the badge map lists, so
        nothing the user cannot see is touched. Returns those room ids."""
        counts = await self.unread_counts(project_id, user_handle)
        await self._repo.mark_read_many(list(counts), user_handle)
        return list(counts)

    NOTIFY_LEVELS = ("all", "mute")

    async def notify_levels(
        self, project_id: uuid.UUID, user_handle: str
    ) -> dict[uuid.UUID, str]:
        if await self._projects.get(project_id) is None:
            raise NotFoundError("Project not found")
        return await self._repo.notify_levels(project_id, user_handle)

    async def set_notify_level(
        self, topic_id: uuid.UUID, user_handle: str, level: str
    ) -> None:
        if level not in self.NOTIFY_LEVELS:
            raise ValidationError(say("topicNotifyLevelInvalid"))
        await self.get_or_404(topic_id)
        await self._repo.set_notify_level(topic_id, user_handle, level)

    # ---- 手动归档 / 取消归档 (归档去向, spec §6.3 extension) -------------

    async def archive(self, topic_id: uuid.UUID, *, by: str) -> Topic:
        """Manually archive a topic (idempotent) — CASCADING: a topic's active
        subtopics go with it (归档整件事，分身是这件事的一部分；漏下的孤儿分身
        没有父上下文，毫无意义). The root topic (项目本体) can't be archived."""
        topic = await self._repo.lock(topic_id)
        if topic is None:
            raise NotFoundError("Topic not found")
        if topic.kind == TopicKind.root:
            raise ValidationError(say("projectRootCannotArchive"))
        if topic.status == TopicStatus.archived:
            return topic
        await self._archive_one(topic, by=by)
        await self._archive_children(topic, by=by)
        await self._session.flush()
        return topic

    async def _archive_children(self, topic: Topic, *, by: str) -> None:
        """Closing a place closes what is inside it.

        Two kinds of inside, and they are not the same thing. Rooms nest one
        level under the root, so the recursion below still applies there. Threads
        do not nest and are not `topics` rows at all — closing the room closes
        them because a piece of work with no room has no context and no way back,
        and the cards they are still waiting on have to be settled with them
        (an unresolved card on a frozen place is one nobody can ever act on).
        """
        for thread in await TaskService(self._session).list_in_room(topic.id):
            if thread.status == TaskStatus.closed:
                continue
            thread.status = TaskStatus.closed
            thread.closed_at = datetime.now(UTC)
            # Why it stopped has to be readable IN the thread that stopped: the
            # room's own archive note lands on the room, and whoever opens the
            # thread later sees work that simply ended mid-sentence.
            landed = landing(
                EventAbout.task,
                project_id=thread.project_id,
                room_id=topic.id,
                task_id=thread.id,
            )
            await self._blocks.add(
                project_id=landed.project_id,
                conversation_id=landed.conversation_id,
                author=by,
                author_type=AuthorType.platform,
                content=say("threadArchivedWithRoom", room=topic.title),
                kind=BlockKind.event,
                meta={"platform": True},
            )
        # 卡上还开着的验收卡由 `_archive_one` 一并收：它们和房间自己的卡挂在同一
        # 个房间上，一次问清比每条活问一次少一半的往返，也不会漏掉一条活被收工
        # 之后才归档的房间。
        children = await self._repo.list_children(topic.id)
        for child in children:
            if child.status == TopicStatus.archived:
                continue
            await self._archive_one(child, by=by, cascaded_from=topic.title)
            await self._archive_children(child, by=by)

    async def _overview_room(self, project_id: uuid.UUID) -> uuid.UUID:
        """项目总览那个房间（`TopicKind.root`）——项目的事落在这里。

        总览是哪个房间只有项目行知道，`landing()` 读不到它，所以这一句由调用点
        负责：从 `Project.root_topic_id` 取，不自己挑一个房间。
        """
        project = await self._projects.get(project_id)
        assert project is not None  # 房间的 project_id 是外键，项目行必在。
        assert project.root_topic_id is not None  # create() 一定播种了总览。
        return project.root_topic_id

    async def _archive_one(
        self, topic: Topic, *, by: str, cascaded_from: str | None = None
    ) -> None:
        topic.status = TopicStatus.archived
        archived_at = datetime.now(UTC)
        topic.archived_at = archived_at
        topic.cleanup_due_at = archived_at + timedelta(
            seconds=settings.topic_archive_cleanup_delay_s
        )
        operation = RoomCleanup(
            id=uuid.uuid4(),
            project_id=topic.project_id,
            topic_id=topic.id,
            resource_id=topic.resource_id or topic.id,
            due_at=topic.cleanup_due_at,
        )
        self._session.add(operation)
        topic.cleanup_id = operation.id
        # 孤儿卡修复 (2026-08-10): 归档必须同时终结这个话题上还没决议的验收卡。
        # 一张骑着 PR 的卡不是"停着"——轮询器每 60 秒还在拿 GitHub 凭据跟进它。
        # 去向与理由见 review/archive.py 的模块 docstring。
        from app.domain.review.archive import close_cards_for_archived_topic

        overview_room = await self._overview_room(topic.project_id)
        await close_cards_for_archived_topic(
            self._session,
            topic_id=topic.id,
            project_id=topic.project_id,
            topic_title=topic.title,
            overview_room_id=overview_room,
            by=by,
        )
        note = (
            say("roomArchivedWithParent", room=topic.title, parent=cascaded_from)
            if cascaded_from
            else say("roomArchived", actor=f"<@{by}>", room=topic.title)
        )
        # 房间归档是项目的事，不是这个房间的事（结论 14）：房间关掉之后没人再打开
        # 它的时间线，而「少了一个房间」恰恰是项目总览要记的一行。
        landed = landing(
            EventAbout.project, project_id=topic.project_id, room_id=overview_room
        )
        await self._blocks.add(
            project_id=landed.project_id,
            conversation_id=landed.conversation_id,
            author=by,
            author_type=AuthorType.platform,
            content=note,
            kind=BlockKind.event,
            meta={"platform": True},
        )

    async def unarchive(self, topic_id: uuid.UUID, *, by: str) -> Topic:
        """Bring an archived topic back to active (idempotent). Accept markers
        are kept — un-archiving doesn't rewrite acceptance history (use 撤回采纳
        for that)."""
        topic = await self._repo.lock(topic_id)
        if topic is None:
            raise NotFoundError("Topic not found")
        if topic.status != TopicStatus.archived:
            return topic
        operation = (
            await self._session.get(RoomCleanup, topic.cleanup_id)
            if topic.cleanup_id
            else None
        )
        if operation is not None:
            if operation.state == "preparing":
                raise ConflictError(say("unarchiveWhileSessionStopping"))
            if operation.state == "pending":
                operation.state = "cancelled"
            elif operation.state in {"claimed", "retained", "complete"}:
                topic.resource_id = uuid.uuid4()
                await AgentSessionService(self._session).forget_room(topic.id)
        topic.status = TopicStatus.active
        topic.archived_at = None
        topic.cleanup_due_at = None
        topic.cleanup_id = None
        # 归档落总览，取消归档就落在同一条线上：一个房间回到项目里，和它离开项目
        # 是同一件事的两面，读的人也在同一个地方读。
        landed = landing(
            EventAbout.project,
            project_id=topic.project_id,
            room_id=await self._overview_room(topic.project_id),
        )
        await self._blocks.add(
            project_id=landed.project_id,
            conversation_id=landed.conversation_id,
            author=by,
            author_type=AuthorType.platform,
            content=say("roomUnarchived", actor=f"<@{by}>", room=topic.title),
            kind=BlockKind.event,
            meta={"platform": True},
        )
        await self._session.flush()
        return topic

    async def archive_with_project(self, project_id: uuid.UUID, *, by: str) -> None:
        """Archive every room of a project being archived — the overview and the
        private chats too, which nobody can archive one at a time.

        Each room goes the way a room archived by hand goes (``_archive_one``):
        its session is stopped and its machine reclaimed after the grace, its
        open cards are settled and its PRs stop being polled, its threads close.
        A project that is archived keeps nothing running because its rooms keep
        nothing running. The rooms are marked so unarchiving the project brings
        back these and only these.
        """
        rooms = [
            topic
            for topic in await self._repo.lock_all_in_project(project_id)
            if topic.status != TopicStatus.archived
        ]
        for topic in rooms:
            # The overview's children were already taken along with it.
            if topic.status != TopicStatus.archived:
                await self._archive_one(topic, by=by)
                await self._archive_children(topic, by=by)
            topic.archived_with_project = True
        await self._session.flush()

    async def unarchive_with_project(self, project_id: uuid.UUID, *, by: str) -> None:
        """Bring back the rooms :meth:`archive_with_project` took along."""
        for topic in await self._repo.lock_all_in_project(project_id):
            if not topic.archived_with_project:
                continue
            await self.unarchive(topic.id, by=by)
            topic.archived_with_project = False
        await self._session.flush()

    async def upgrade_block_to_place(
        self,
        *,
        block_id: uuid.UUID,
        created_by: str | None = None,
    ) -> tuple[Topic, Task | None, bool]:
        """讨论升级 (eval A1): turn a block into work of its own; the original
        position becomes a live link.

        WHICH place depends on where the block is, and the two answers are not
        interchangeable:

        - in a room, the message becomes a TASK in the same room, owned by
          whoever upgraded it. Its agent drafts the task's document from the
          message and what was said around it (the caller hands it that).
        - in a private chat, it becomes a real room under the project root.
          Private chats are not in the topic tree (`list_for_project` hides
          them), so a thread there would be a thread nobody but its owner could
          ever open.

        Returns (room, task, created): `task` is None when the upgrade made a
        room; created=False on an idempotent re-upgrade, so the caller doesn't
        start the task's agent twice."""
        block = await self._blocks.get(block_id)
        if block is None:
            raise NotFoundError("Block not found")
        tasks = TaskService(self._session)
        # Idempotent: a second 升级 on the same block just returns what it
        # already created (so a double-click navigates instead of erroring).
        if block.upgraded_to_task_id is not None:
            existing_task = await tasks.get(block.upgraded_to_task_id)
            if existing_task is not None:
                room = await self._repo.get(existing_task.room_id)
                if room is not None:
                    return room, existing_task, False
        if block.upgraded_to_topic_id is not None:
            existing_room = await self._repo.get(block.upgraded_to_topic_id)
            if existing_room is not None:
                return existing_room, None, False
        parent = await self._repo.get(
            await room_of(self._session, block.conversation_id)
        )
        if parent is None:
            raise NotFoundError("Parent topic not found")
        # 归档后工作面冻结 (spec §6.3) — consistent with dispatch/edit_doc.
        if parent.status == TopicStatus.archived:
            raise ValidationError(say("topicArchivedFrozen"))

        project = await self._projects.get(block.project_id)

        if not parent.is_private:
            # Opened unnamed: its agent names it (`cheese_title`).
            task = await self.create_task(room_id=parent.id, created_by=created_by)
            task.upgraded_from_block_id = block.id
            await self._blocks.set_upgraded_to_place(block, task_id=task.id)
            return parent, task, True

        # 私聊不是话题树的父节点 (spec §1).
        # A private chat's doc is never copied into a public place, so the new
        # room's brief carries the upgraded message and nothing else.
        brief = _brief_doc(
            child_title=PLACEHOLDER_TITLE,
            parent_title=parent.title,
            created_by=created_by,
            source_block=block.content,
        )
        root_id = project.root_topic_id if project else None
        # Titles are AI-generated (the agent names a topic via `cheese_title`),
        # never derived from text — see CLAUDE.md. An upgraded block starts
        # unnamed and 芝士 names it on its first turn, like a + new topic.
        new_room = await self._repo.add(
            project_id=block.project_id,
            title=None,
            parent_id=root_id,
            kind=TopicKind.topic,
            created_by=created_by,
            upgraded_from_block_id=block.id,
        )
        # Same fallback ladder as create()/dispatch_task — 升级 is usually the
        # 分身's own suggestion, and ``created_by`` is None whenever the caller is
        # not a verified person (the route passes only an authenticated actor's
        # handle). Passing that straight to seed() — which drops None and every
        # agent handle — would mint an ownerless room.
        await self._members.seed(
            new_room.id,
            agent_handle=await self._starting_agent_handle(new_room),
            owner_handle=await self._resolve_owner(
                created_by,
                project_id=block.project_id,
                parent_id=root_id,
                project_owner=project.owner_handle if project else None,
            ),
        )
        await self._blocks.set_upgraded_to_place(block, topic_id=new_room.id)
        await self.seed_brief_doc(new_room, brief)
        return new_room, None, True

    async def seed_brief_doc(self, topic: Topic, content: str) -> None:
        """Preset a newborn ROOM's living doc with its task brief. Author is
        `system`: the platform assembled it from existing text — nothing here
        speaks as 芝士 (the room's kickoff turn writes the real opening).

        A room only: a task's document is its own (`TaskService.ensure_document`),
        written by the task's session.
        """
        doc = await self.room_doc(topic.id, topic.project_id)
        await DocumentJournal(self._session).lock(doc.id)
        await DocumentWriter(self._session, summarize_doc_change).seed(doc, content)

    async def _card_block(self, room: Topic, task: Task, *, actor: str) -> Block:
        """The room's timeline says a task was created here, and whose it is.

        The task id rides in `meta`, and the STATUS does not: a block is a fixed
        record of a moment, and a piece of work that reads `running` forever
        after it finished is worse than no status at all. Whoever renders this
        reads the live row (`GET /topics/{id}/tasks`) for that.
        """
        # The ROOM's main line: the whole point is that the room sees the task.
        landed = landing(EventAbout.room, project_id=room.project_id, room_id=room.id)
        return await self._blocks.add(
            project_id=landed.project_id,
            conversation_id=landed.conversation_id,
            author="system",
            author_type=AuthorType.platform,
            content=say(
                "taskCreated",
                actor=f"<@{actor}>",
                title=task.title,
                owner=f"<@{task.owner_handle}>" if task.owner_handle else "",
            ),
            kind=BlockKind.event,
            meta={"platform": True, "action": "task_created", "task_id": str(task.id)},
        )

    async def create_task(
        self,
        *,
        room_id: uuid.UUID,
        created_by: str | None,
        title: str | None = None,
        owner_handle: str | None = None,
    ) -> Task:
        """Open a task in a room: a conversation of its own, owned by one person,
        with an empty living document for its agent to draft.

        The owner is the person named, else the person creating it, else — when
        no person is identifiable — the room's owner, then the project's. A task
        belongs to someone; a task nobody owns is one nobody can start or answer
        for. Who reviews its changes is decided when it is started, not here.
        """
        place = await PlaceResolver(self._session).resolve(room_id)
        if place is None:
            raise NotFoundError(say("topicNotFound"))
        room = place.room
        # 归档后工作面冻结 (spec §6.3): no new work in a frozen room.
        if room.status == TopicStatus.archived:
            raise ValidationError(say("topicArchivedFrozen"))
        project = await self._projects.get(room.project_id)
        owner = (
            owner_handle
            if names_a_person(owner_handle)
            else created_by
            if names_a_person(created_by)
            else await self._resolve_owner(
                None,
                project_id=room.project_id,
                parent_id=room.id,
                project_owner=project.owner_handle if project else None,
            )
        )
        tasks = TaskService(self._session)
        named = (title or "").strip()
        task = await tasks.open_thread(
            project_id=room.project_id,
            room_id=room.id,
            title=named[:300] or PLACEHOLDER_TITLE,
            title_source=TaskTitleSource.human
            if named
            else TaskTitleSource.placeholder,
            owner_handle=owner,
            created_by=created_by,
        )
        await tasks.ensure_document(task)
        await self._card_block(room, task, actor=created_by or "system")
        return task

    async def clone_from(
        self, *, target_topic_id: uuid.UUID, source_topic_id: uuid.UUID
    ) -> Topic:
        """Deep-copy the source topic's Claude conversation onto the target topic
        (transcript-fork, fusion-design §6 clone — distinct from a task).

        A task (create_task) starts a FRESH session of its own; clone instead
        forks the source's full conversation state so the target resumes
        exactly where the source is."""
        target = await self.get_or_404(target_topic_id)
        source = await self.get_or_404(source_topic_id)
        if source.id == target.id:
            raise ValidationError(say("topicCloneIntoSelf"))
        if source.project_id != target.project_id:
            # Session dirs + workdir slugs are keyed per project; a cross-project
            # clone would point the transcript at a different repo. Keep in-project.
            raise ValidationError(say("topicCloneSameProject"))
        # The agent working in the source is the one whose conversation this
        # forks, and the agent working in the target is the one that inherits it.
        # Both are resolved rather than assumed: a room may host several, and
        # copying one agent's transcript onto another's key would hand it a
        # thread it never had.
        sessions = AgentSessionService(self._session)
        source_agent = await self.resolve_agent(source)
        target_agent = await self.resolve_agent(target)
        # 骨架不是参与者的属性（结论 28），是项目设置加部署设置答的一件事。源和
        # 目标同属一个项目（上面已经拒了跨项目），所以两边解析出来的是同一个，
        # 「两边骨架不同」这个问题在这里不存在。
        owner = await self._projects.get(target.project_id)
        harness = harness_for(owner.settings if owner else None)
        source_sid = await sessions.resume_token(
            source.id, source_agent.handle, harness=harness
        )
        if not source_sid:
            raise ValidationError(say("topicCloneNeverRan"))
        new_sid = clone.mint_session_id()
        try:
            clone.clone_transcript_files(
                source_session_dir=ws.session_dir(source.project_id, source.id),
                source_session_id=source_sid,
                target_session_dir=ws.session_dir(target.project_id, target.id),
                new_session_id=new_sid,
                # Claude resolves --resume under the slug of the cwd it runs
                # with, so the fork must land under the TARGET topic's workdir.
                target_cwd=ws.sandbox_topic_workdir(target.id),
            )
        except FileNotFoundError as exc:
            raise ValidationError(say("topicCloneRecordMissing")) from exc
        # Point the target at the forked session so its next turn --resume's it.
        await sessions.remember(
            conversation_id=target.id,
            agent_handle=target_agent.handle,
            resume_token=new_sid,
            harness=harness,
        )
        return target

    async def place_or_404(self, place_id: uuid.UUID) -> Place:
        """The conversation this id names — a room, or one of its tasks — or 404.

        The route-level counterpart of `get_or_404`. A task's place carries its
        room: the roster, files and work computer are the room's, the history
        and the session are the task's.
        """
        place = await PlaceResolver(self._session).conversation(place_id)
        if place is None:
            raise NotFoundError("Topic not found")
        return place

    async def doc_of_room(self, room_id: uuid.UUID) -> Document | None:
        """这间房自己那份实况文档；还没有人打开或写过它时没有。

        和 `get_doc` 读的是同一处，差别只在手上是什么：路由手上是个可能不存在的
        place，所以先 404；轮末那种「房间行已经读出来了」的地方手上就是房间 id，
        不必再绕一圈（`topic/doc_nudge.py`）。
        """
        return await Documents(self._session).of_room(room_id)

    async def get_doc(self, topic_id: uuid.UUID) -> Document | None:
        place = await self.place_or_404(topic_id)
        return await self.doc_of_room(place.room_id)

    async def doc_nodes(self, doc: Document) -> list[DocumentNode]:
        """文档的顶层块（标题、段落、列表……），按顺序。"""
        return await Documents(self._session).nodes(doc)

    async def room_doc(self, room_id: uuid.UUID, project_id: uuid.UUID) -> Document:
        """这间房的实况文档，没有就建一份空的（第 0 版）：要给它签票、写它、在它
        上面评论的人，手上得先有它的编号。"""
        return await Documents(self._session).ensure_for_room(
            room_id=room_id, project_id=project_id
        )

    async def overview_auto(self, topic_id: uuid.UUID) -> list[dict]:
        """总览房间（项目根话题）的 ②③，结构化（#1889）。

        总览只属于根话题：别的房间读得到的是它们自己的实况文档，没有人从那里看
        项目全局。非根话题给的是一句 404 —— 它名下确实没有这么一件东西，这和
        「有这个地方但你看不到」是两回事。
        """
        place = await self.place_or_404(topic_id)
        project = await self._projects.get(place.project_id)
        if project is None or project.root_topic_id != place.room_id:
            raise NotFoundError("Topic not found")
        data = await self.overview_auto_data(place.project_id)
        return overview_auto_blocks(**data)

    async def overview_auto_data(
        self,
        project_id: uuid.UUID,
        *,
        all_topics: list[Topic] | None = None,
        roster: list[dict] | None = None,
    ) -> dict[str, list[dict]]:
        """②③ 的每一行：活跃话题、已结束话题的结论。

        全部来自结构化数据，所以**没有一句是手抄的**——谁改了源头，下一次就是
        新的。负责人取该话题最新那张任务卡的 owner：一个房间可以有好几张卡，最新
        的那张才说得出现在谁在做。

        总览房间的提示词注入（`agent/chat.py`）和前端那一栏
        （`GET /topics/{id}/overview`）读的是**同一次取数**：两个读者，一份来源。
        手上已经有话题表和名册的调用方传进来，省掉把同一张名册再读一遍。
        """
        if all_topics is None:
            all_topics = await self._repo.list_for_project(project_id)
        if roster is None:
            roster = await roster_rows(self._session, project_id)

        name_of = {m["handle"]: m["name"] for m in roster}

        def person(handle: str | None) -> str | None:
            # 名册上的名字才是 @ 得到的名字；查不到就照 handle 写，那是真的。
            return f"@{name_of.get(handle, handle)}" if handle else None

        latest_card: dict[uuid.UUID, Task] = {}
        for card in await TaskService(self._session).list_in_project(project_id):
            # Oldest first: the newest card in each room wins.
            latest_card[card.room_id] = card

        def card_state(topic_id: uuid.UUID) -> str:
            card = latest_card.get(topic_id)
            if card is None:
                return "还没开活"
            return "在做" if card.status == TaskStatus.open else "已收工"

        live = [
            t
            for t in all_topics
            if t.kind != TopicKind.root and t.status != TopicStatus.archived
        ][:ACTIVE_TOPICS_LIMIT]
        closed = sorted(
            (
                t
                for t in all_topics
                if t.kind != TopicKind.root and t.status == TopicStatus.archived
            ),
            key=lambda t: t.archived_at or t.updated_at,
            reverse=True,
        )[:CLOSED_TOPICS_LIMIT]
        # 只取要渲染的那几间房的文档：一屏之外的结论没人读，问了也是白问。
        docs = await Documents(self._session).of_rooms([t.id for t in (*live, *closed)])

        def conclusion(topic: Topic, *, prefer_card: bool) -> str | None:
            """话题现在的一句话结论：卡上那句优先，没有就看它自己的实况文档。"""
            card = latest_card.get(topic.id)
            if prefer_card and card is not None and card.conclusion:
                return first_sentence(card.conclusion)
            doc = docs.get(topic.id)
            return topic_status(doc.content) if doc is not None else None

        return {
            ACTIVE_TOPICS_KEY: [
                {
                    "id": str(t.id),
                    "title": t.title,
                    "owner": person(
                        card.owner_handle
                        if (card := latest_card.get(t.id)) is not None
                        else None
                    ),
                    "status": card_state(t.id),
                    "conclusion": conclusion(t, prefer_card=False),
                }
                for t in live
            ],
            CLOSED_TOPICS_KEY: [
                {
                    "id": str(t.id),
                    "title": t.title,
                    "conclusion": conclusion(t, prefer_card=True),
                }
                for t in closed
            ],
        }

    async def get_progress(
        self, conversation_id: uuid.UUID
    ) -> tuple[list[dict], datetime | None]:
        """进度层 (#187): the checklist this topic's work left behind.

        Returns ``([], None)`` for a topic that never had one — an empty
        checklist and no checklist are the same thing to a reader, and making
        the caller handle a null row buys nothing.
        """
        row = await TopicProgressRepository(self._session).get(conversation_id)
        if row is None:
            return [], None
        return [dict(item) for item in row.items], row.updated_at

    def require_doc_writable(self, place: Place) -> None:
        """归档后工作面冻结 (spec §6.3): an archived room's document takes no
        more writes."""
        if place.room.status == TopicStatus.archived:
            raise ValidationError(say("topicArchivedDocFrozen"))
