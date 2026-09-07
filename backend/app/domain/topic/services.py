"""Topic business logic — rooms, and the work dispatched inside them.

A block can be upgraded into a place of its own (讨论升级), a room can dispatch
a piece of work as a thread (从上往下拆解), and a thread's conclusion flows back
to the room it sits in (结论回流).

The tree is one level deep now: the project root has rooms, and rooms have no
topic children at all. What used to be a third level is a `tasks` row — see
`app.domain.room_task.place` for how one id still addresses either.
"""

import difflib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import overload

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.domain.agent import clone
from app.domain.agent_instance.services import (
    IMPLICIT_DEFAULT,
    AgentInstanceService,
    ResolvedAgent,
)
from app.domain.agent_session.services import AgentSessionService
from app.domain.alert.services import AlertService
from app.domain.block.doc_tree import markdown_to_nodes
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.identity.handles import looks_like_agent_handle, names_a_person
from app.domain.membership.services import MemberService
from app.domain.project.models import ProjectRole
from app.domain.project.repositories import ProjectRepository
from app.domain.review.services import AcceptService
from app.domain.room_task.models import Task, TaskStatus
from app.domain.room_task.place import Place, PlaceResolver
from app.domain.room_task.services import ClaimService, TaskService
from app.domain.topic.models import Topic, TopicKind, TopicRole, TopicStatus
from app.domain.topic.repositories import (
    SortOrder,
    TopicProgressRepository,
    TopicRepository,
    TopicSortField,
)
from app.domain.topic_membership.services import TopicMemberService
from app.domain.workspace import service as ws

# Titles are AI-generated (the agent names a topic via `cheese title`), never
# deterministically derived from text — see CLAUDE.md. An upgraded block starts
# untitled and 芝士 names it on its first turn (same as a + new topic).
PLACEHOLDER_TITLE = "新话题"


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
        raise ValidationError("房间之下是「一件活」，不是另一个房间——用拆活")


logger = logging.getLogger("cheesex.topic")


def _doc_conflict(current_version: int) -> ConflictError:
    """The living doc moved under a writer. The current version rides along so
    the caller can re-read and rebase without a second round trip."""
    return ConflictError(
        "实况文档已经被改过了，你手上这份是旧的",
        data={"doc_version": current_version},
    )


def _brief_doc(
    *,
    child_title: str,
    parent_title: str,
    brief: str | None,
    parent_doc: str | None,
    created_by: str | None,
    source_block: str | None = None,
) -> str:
    """The newborn sub-topic's initial living doc: a task brief.

    Pure assembly of EXISTING text — the splitter's brief (AI- or human-written),
    the upgraded block (for 讨论升级), and the parent's living doc, all copied
    verbatim under fixed structural headings. No semantics are derived from
    prose and nothing speaks as 芝士 (CLAUDE.md red line); the 分身 rewrites
    this into its own status summary on its kickoff turn."""
    by = f"由 {created_by} " if created_by else ""
    origin = "升级" if source_block is not None else "拆出"
    parts = [
        f"# {child_title}",
        f"> 任务简报（{by}从「{parent_title}」{origin}时自动预置；"
        "分身开工后会把这份文档改写成状态摘要）",
    ]
    if source_block is not None:
        # 讨论升级: the upgraded block IS the task statement.
        parts += ["## 升级来源（这段讨论就是任务）", source_block.strip() or "（空）"]
    else:
        parts += [
            "## 拆分意图",
            brief.strip()
            if brief and brief.strip()
            else "（拆分时没有附说明——任务以标题和下面的父话题文档为准）",
        ]
    parts += [
        "## 父话题当时的实况文档（快照，供参考）",
        parent_doc.strip()
        if parent_doc and parent_doc.strip()
        else "（父话题当时还没有实况文档）",
    ]
    return "\n\n".join(parts)


def _is_mid_turn_block(block: Block) -> bool:
    """Is this block the middle of a turn rather than the end of one?

    A tool action by 芝士 — `meta.tool` is stamped by the 现场 event writer, so
    this asks what the block IS rather than parsing its text. Nothing healthy
    leaves one as a topic's last word: the turn either keeps working (another
    action, a message) or fails into a system event.
    """
    return (
        block.kind == BlockKind.event
        and block.author_type == AuthorType.ai
        and bool((block.meta or {}).get("tool"))
    )


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
        self._alerts = AlertService(session)

    async def get(self, topic_id: uuid.UUID) -> Topic | None:
        """Return one topic for cross-domain service callers."""
        return await self._repo.get(topic_id)

    async def resolve_agent(self, topic: Topic) -> ResolvedAgent:
        """Which agent works in this room — its own, else the project's."""
        project = await self._projects.get(topic.project_id)
        if project is None:
            return IMPLICIT_DEFAULT
        return await AgentInstanceService(self._session).for_topic(topic, project)

    async def set_agent(
        self, topic_id: uuid.UUID, instance_id: uuid.UUID | None
    ) -> tuple[Topic, ResolvedAgent]:
        """Hand this topic to a different agent.

        Nothing is destroyed. A conversation is keyed by (topic, agent) in
        ``agent_sessions``, so the incoming agent looks up a key that has no row
        and starts fresh, the outgoing agent's row stays exactly where it is, and
        handing the topic back finds it again. It used to be otherwise — one
        session column per topic meant a switch had to erase the thread, and the
        caller had to warn about it first.

        Passing ``None`` hands the topic back to the project's default.
        """
        topic = await self.get_or_404(topic_id)
        agents = AgentInstanceService(self._session)
        instance = (
            await agents.get_in_project(
                project_id=topic.project_id, instance_id=instance_id
            )
            if instance_id is not None
            else None
        )
        wanted = instance.id if instance is not None else None
        if topic.agent_instance_id != wanted:
            topic.agent_instance_id = wanted
            await self._session.flush()
        return topic, await self.resolve_agent(topic)

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        title: str,
        parent_id: uuid.UUID | None = None,
        created_by: str | None = None,
        agent_instance_id: uuid.UUID | None = None,
    ) -> Topic:
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
        # An explicit agent is checked to be this project's; NULL means "the
        # project's default", which follows the project if that default changes
        # later — a copy taken now would silently stop following it.
        if agent_instance_id is not None:
            await AgentInstanceService(self._session).get_in_project(
                project_id=project_id, instance_id=agent_instance_id
            )
        topic = await self._repo.add(
            project_id=project_id,
            title=title,
            parent_id=parent_id,
            kind=kind,
            created_by=created_by,
            agent_instance_id=agent_instance_id,
        )
        # No branch parent: this path only ever makes ROOMS now, and a room forks
        # the base branch. Binding one to its parent would have made the project
        # root a fork point, which nothing has ever wanted.
        #
        # 群聊房间的地基 (fusion-design §3): seed the roster — creator = owner,
        # 芝士 joins as a member. `created_by` alone is not enough: 芝士 itself
        # creating a topic, or a caller whose token didn't resolve (anonymous
        # Phase-0), would leave the room OWNERLESS — seed() deliberately skips
        # "cheese"/None as owner — and then nobody can manage its roster, and
        # every sub-topic split beneath it inherits the same emptiness
        # (dispatch_task falls back to the PARENT's owner). Nearly the same
        # ladder as dispatch_task, which has one rung this does not: a split
        # happens DURING somebody's turn, so it can ask who is driving. Creating a
        # topic is a standalone act with nobody to ask.
        await self._members.seed(
            topic.id,
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
        parent room's owner, else the project's owner, else the project's 组长.

        That last rung is not decoration. Measured on the dogfooding project
        2026-08-12, answering 「新话题的拥有者为什么有的有，有的是空的」: the
        project's ``owner_handle`` is NULL and its root topic is ownerless too,
        so every topic 芝士 opened under the root fell through all three rungs
        and came out blank — five active rooms with no one able to manage the
        roster. The ladder was right; its bottom had nothing to stand on. The
        roster did: that project has a ``lead``, which is exactly "who is in
        charge here" already recorded, not a new policy invented to fill a hole.

        Returns None only when even the roster has no lead — the caller still
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
        return project_owner or await self._project_lead(project_id)

    async def _project_lead(self, project_id: uuid.UUID) -> str | None:
        """The project's 组长, as the last rung of the ownership ladder.

        Read lazily — only when the rungs above came up empty — so an ordinary
        topic-create still costs no extra query. Through the roster's *service*,
        not its repository: `test_domain_import_guard` forbids the shortcut, and
        the service is also where "who counts as a member" is decided.
        """
        members, _ = await MemberService(self._session).list_for_project(project_id)
        return next(
            (m.user_handle for m in members if m.role == ProjectRole.lead),
            None,
        )

    async def get_or_create_private(
        self,
        *,
        project_id: uuid.UUID,
        user_handle: str,
        peer_handle: str | None = None,
    ) -> Topic:
        """A 1:1 private chat (spec §1).

        No ``peer_handle`` → the member's 1:1 with 芝士. With ``peer_handle`` →
        a person-to-person DM between the two humans (shared by both).
        """
        if await self._projects.get(project_id) is None:
            raise NotFoundError("Project not found")
        topic = await self._repo.get_or_create_private(
            project_id=project_id, user_handle=user_handle, peer_handle=peer_handle
        )
        await TopicMemberService(self._session).seed_private(
            topic.id,
            owner_handle=topic.private_owner or user_handle,
            peer_handle=topic.private_peer,
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
    ) -> tuple[list[Topic], int]:
        topics = await self._repo.list_for_project(
            project_id,
            sort=sort,
            order=order,
            active_since=_as_utc(active_since),
        )
        # `total` counts what the caller got: a filtered page whose total still
        # said "all topics" would tell a paging client to keep asking for rows
        # that do not exist.
        total = (
            len(topics)
            if active_since is not None
            else await self._repo.count_for_project(project_id)
        )
        return topics, total

    async def last_activity_for_topics(
        self, topic_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, datetime]:
        return await self._repo.last_activity_for_topics(topic_ids)

    async def relevance_for_topics(
        self, topics: list[Topic], viewer_handle: str | None
    ) -> dict[uuid.UUID, TopicRelevance]:
        """{topic_id: 与我的相关性} for a batch of topics (C2).

        THREE queries, whatever the batch size — one per way of being involved
        that lives in another table (roster, accept cards, @-notifications).
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
        mentions = await self._alerts.mention_topic_ids(topic_ids, viewer_handle)
        relevance: dict[uuid.UUID, TopicRelevance] = {}
        for topic in topics:
            awaits = cards.get(topic.id, False) or mentions.get(topic.id, False)
            participates = (
                topic.id in roster
                or topic.created_by == viewer_handle
                or topic.id in cards
                or topic.id in mentions
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
        # The room's OWN line: a card's chatter is its 分身 working, and a
        # room that has gone quiet while one of them talks is still quiet.
        last = await self._blocks.latest_for_topic(place.room_id)
        silent_for_s = (
            None
            if last is None
            else round((datetime.now(UTC) - _as_utc(last.created_at)).total_seconds())
        )
        signal: dict = {
            "stalled": False,
            "reason": None,
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

    async def mark_read(self, topic_id: uuid.UUID, user_handle: str) -> None:
        await self.get_or_404(topic_id)
        await self._repo.mark_read(topic_id, user_handle)

    # ---- 手动归档 / 取消归档 (归档去向, spec §6.3 extension) -------------

    async def archive(self, topic_id: uuid.UUID, *, by: str) -> Topic:
        """Manually archive a topic (idempotent) — CASCADING: a topic's active
        subtopics go with it (归档整件事，分身是这件事的一部分；漏下的孤儿分身
        没有父上下文，毫无意义). The root topic (项目本体) can't be archived."""
        topic = await self.get_or_404(topic_id)
        if topic.kind == TopicKind.root:
            raise ValidationError("项目本体不能归档")
        if topic.status == TopicStatus.archived:
            await self._release_cloud_machine(topic.id)
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
        for task in await TaskService(self._session).threads_for_room(
            topic.id, limit=0
        ):
            thread = task[0]
            if thread.status == TaskStatus.closed:
                continue
            thread.status = TaskStatus.closed
            thread.closed_at = datetime.now(UTC)
            # Why it stopped has to be readable IN the thread that stopped: the
            # room's own archive note lands on the room, and whoever opens the
            # thread later sees work that simply ended mid-sentence.
            await self._blocks.add(
                project_id=thread.project_id,
                topic_id=topic.id,
                task_id=thread.id,
                author=by,
                author_type=AuthorType.system,
                content=f"随父话题「{topic.title}」一同归档",
                kind=BlockKind.event,
                meta={"platform": True},
            )
        # 卡上还开着的验收卡由 `_archive_one` 一并收：它们和房间自己的卡挂在同一
        # 个房间上，一次问清比每条活问一次少一半的往返，也不会漏掉一条活被收工
        # 之后才归档的房间。
        children = await self._repo.list_children(topic.id)
        for child in children:
            if child.status == TopicStatus.archived:
                await self._release_cloud_machine(child.id)
                continue
            await self._archive_one(child, by=by, cascaded_from=topic.title)
            await self._archive_children(child, by=by)

    async def _archive_one(
        self, topic: Topic, *, by: str, cascaded_from: str | None = None
    ) -> None:
        topic.status = TopicStatus.archived
        topic.archived_at = datetime.now(UTC)
        await self._release_cloud_machine(topic.id)
        await self._retire_storage(topic)
        # 孤儿卡修复 (2026-08-10): 归档必须同时终结这个话题上还没决议的验收卡。
        # 一张 `pr_open` 的卡不是"停着"——轮询器每 60 秒还在用当初批准人的
        # GitHub token 推进它。去向与理由见 review/archive.py 的模块 docstring。
        from app.domain.review.archive import close_cards_for_archived_topic

        await close_cards_for_archived_topic(
            self._session,
            topic_id=topic.id,
            project_id=topic.project_id,
            topic_title=topic.title,
            by=by,
        )
        note = (
            f"随父话题「{cascaded_from}」一同归档"
            if cascaded_from
            else f"<@{by}> 归档了话题"
        )
        await self._blocks.add(
            project_id=topic.project_id,
            topic_id=topic.id,
            author=by,
            author_type=AuthorType.system,
            content=note,
            kind=BlockKind.event,
            meta={"platform": True},
        )

    async def _release_cloud_machine(self, topic_id: uuid.UUID) -> None:
        """Archive is the Cloud VM's sole reclamation lifecycle (#442 decision 3)."""
        from app.domain.machine.services import MachineService

        await MachineService(self._session).release_topic_machine(topic_id)

    async def _retire_storage(self, topic: Topic) -> None:
        """Stop the room's session on its device (topic/retire.py). Nothing
        comes off disk here: the worktree and the home stay for the retention
        so that an un-archive resumes with its session, and the storage sweep
        takes them after. Best-effort inside; the archive is a fact about the
        place, not about its machine.

        Only the room. The threads closed alongside it have no session to stop:
        each is a 分身 inside this very session, and it goes when this does.
        """
        from app.domain.topic.retire import retire_room_storage

        await retire_room_storage(topic)

    async def unarchive(self, topic_id: uuid.UUID, *, by: str) -> Topic:
        """Bring an archived topic back to active (idempotent). Accept markers
        are kept — un-archiving doesn't rewrite acceptance history (use 撤回采纳
        for that)."""
        topic = await self.get_or_404(topic_id)
        if topic.status != TopicStatus.archived:
            return topic
        topic.status = TopicStatus.active
        topic.archived_at = None
        await self._blocks.add(
            project_id=topic.project_id,
            topic_id=topic.id,
            author=by,
            author_type=AuthorType.system,
            content=f"<@{by}> 取消归档，话题恢复活跃",
            kind=BlockKind.event,
            meta={"platform": True},
        )
        await self._session.flush()
        return topic

    async def upgrade_block_to_place(
        self, *, block_id: uuid.UUID, created_by: str | None = None
    ) -> tuple[Topic, Task | None, bool]:
        """讨论升级 (eval A1): turn a block into work of its own; the original
        position becomes a live link. The upgraded block itself is the task
        statement, preset (with a room-doc snapshot) as the new place's living
        doc; the 分身's auto-kickoff writes its own opening — same mechanics as
        dispatch, no canned template.

        WHICH place depends on where the block is, and the two answers are not
        interchangeable:

        - in a room, the message becomes a piece of WORK — a thread in the same
          room. That is what upgrading a message has always meant in practice
          ("this needs doing"), and it no longer costs a room to say it.
        - in a private chat, it becomes a real room under the project root.
          Private chats are not in the topic tree (`list_for_project` hides
          them), so a thread there would be a thread nobody but its owner could
          ever open.

        Returns (room, card, created): `card` is None when the upgrade made a
        room; created=False on an idempotent re-upgrade, so the caller doesn't
        kick the 分身 off twice."""
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
        parent = await self._repo.get(block.topic_id)
        if parent is None:
            raise NotFoundError("Parent topic not found")
        # 归档后工作面冻结 (spec §6.3) — consistent with dispatch/edit_doc.
        if parent.status == TopicStatus.archived:
            raise ValidationError("话题已归档（工作面冻结），请从结论升级成新话题")

        project = await self._projects.get(block.project_id)

        if not parent.is_private:
            task = await tasks.open_thread(
                project_id=block.project_id,
                room_id=parent.id,
                title=PLACEHOLDER_TITLE,
                owner_handle=await self._resolve_owner(
                    created_by,
                    project_id=block.project_id,
                    parent_id=parent.id,
                    project_owner=project.owner_handle if project else None,
                ),
                created_by=created_by,
            )
            task.upgraded_from_block_id = block.id
            # 升级来的活，任务陈述就是那条消息本身 —— stored on the card, the
            # same place a dispatched brief goes.
            task.brief = block.content
            await self._blocks.set_upgraded_to_place(block, task_id=task.id)
            await self._card_block(parent, task)
            return parent, task, True

        # 私聊不是话题树的父节点 (spec §1).
        # A private chat's doc is never copied into a public place, so the new
        # room's brief carries the upgraded message and nothing else.
        brief = _brief_doc(
            child_title=PLACEHOLDER_TITLE,
            parent_title=parent.title,
            brief=None,
            parent_doc=None,
            created_by=created_by,
            source_block=block.content,
        )
        root_id = project.root_topic_id if project else None
        new_room = await self._repo.add(
            project_id=block.project_id,
            title=PLACEHOLDER_TITLE,
            parent_id=root_id,
            kind=TopicKind.topic,
            created_by=created_by,
            upgraded_from_block_id=block.id,
            agent_instance_id=parent.agent_instance_id,
        )
        # Same fallback ladder as create()/dispatch_task — 升级 is usually the
        # 分身's own suggestion, and this route does not resolve an actor at all
        # (it trusts body.created_by, which the web UI leaves empty when its
        # session token is missing). Passing that straight to seed() — which drops
        # None and every agent handle — was the last path still minting ownerless
        # rooms after create()/dispatch were fixed.
        await self._members.seed(
            new_room.id,
            owner_handle=await self._resolve_owner(
                created_by,
                project_id=block.project_id,
                parent_id=root_id,
                project_owner=project.owner_handle if project else None,
            ),
        )
        await self._blocks.set_upgraded_to_place(block, topic_id=new_room.id)
        await self._seed_brief_doc(new_room, brief)
        return new_room, None, True

    async def _seed_brief_doc(self, topic: Topic, content: str) -> None:
        """Preset a newborn ROOM's living doc with its task brief. Author is
        `system`: the platform assembled it from existing text — nothing here
        speaks as 芝士 (the room's kickoff turn writes the real opening).

        A room only. A piece of work used to get one of these too, and it was
        the one document on the platform nobody could maintain: the worker doing
        the work is a subagent holding the ROOM's token, which cannot reach a
        thread's document address at all. So the brief froze at the moment of
        dispatch and stayed there while the work moved on. A brief belongs on
        the card (`Task.brief`), where being unchangeable is the point.
        """
        doc = await self._blocks.add(
            project_id=topic.project_id,
            topic_id=topic.id,
            author="system",
            author_type=AuthorType.system,
            content=content,
            kind=BlockKind.doc,
        )
        await self._sync_doc_nodes(doc, content)

    async def _card_block(self, room: Topic, task: Task) -> Block:
        """那张卡 —— the room's timeline says a piece of work went out from here.

        This is the edge #184 asked for and #314 had to work around: dispatch
        wrote nothing at all on the main line, so the frontend derived a marker
        from the thread's `room_id` + `created_at` and could only ever say
        "something was dispatched around now". A block can say which one.

        The task id rides in `meta`, and the STATUS does not: a block is a fixed
        record of a moment, and a piece of work that reads `running` forever
        after it finished is worse than no status at all. Whoever renders this
        reads the live row (`GET /topics/{id}/tasks`) for that, which is also
        where the derived markers get theirs — one answer, not two.
        """
        return await self._blocks.add(
            project_id=room.project_id,
            topic_id=room.id,
            # The ROOM's main line: the whole point is that the room sees the
            # work leave. Filing it on the thread would put it exactly where
            # everyone not doing the work is not looking.
            task_id=None,
            author="system",
            author_type=AuthorType.system,
            content=f"派出一条活：{task.title}",
            kind=BlockKind.event,
            meta={"platform": True, "action": "split", "task_id": str(task.id)},
        )

    async def dispatch_task(
        self,
        *,
        place_id: uuid.UUID,
        title: str,
        created_by: str | None = None,
        brief: str | None = None,
        paths: list[str] | None = None,
        triggered_by: str | None = None,
    ) -> Task:
        """从上往下拆解 (eval A2): open a new thread of work in a room.

        The thread is born with a TASK BRIEF as its living doc (分身靠文档保持
        一致, spec §8.4): the splitter's `brief` plus a verbatim snapshot of the
        ROOM's living doc. No canned opening message: the worker writes its own
        (复述确认), because 语义内容必须由 AI 生成 (see CLAUDE.md).

        No worker is started here, and none is started for us. The caller spawns
        one in its own session and binds it (`/tasks/{id}/bind`), so a thread
        exists for a moment with nobody on it — which is also the state a thread
        stays in if the caller never gets around to it.

        `place_id` is wherever the splitter was standing, which may itself be a
        thread: 芝士 working on one piece of work often finds a second. Work does
        not nest, so the new thread hangs in the same ROOM either way — the same
        answer `_child_kind` used to give by refusing to look at whether a parent
        was itself work, except now it is stated rather than accidental.

        `triggered_by` is the human whose turn asked for this split, which the
        caller reads off the runner (`AgentWorkRunner.turn_author_for`) — see the
        ownership ladder below for why it outranks the room's owner."""
        place = await PlaceResolver(self._session).resolve(place_id)
        if place is None:
            raise NotFoundError("话题不存在")
        room = place.room
        # 归档后工作面冻结 (spec §6.3): no new work in a frozen room —
        # follow-up work starts a new topic from the conclusion (升级), not here.
        if room.status == TopicStatus.archived:
            raise ValidationError("话题已归档（工作面冻结），请从结论升级成新话题")
        # The roster is read for the ownership ladder below and for nothing else.
        # A thread does NOT get one: 唯一的主 is the whole difference between a
        # piece of work and the room it happens in, and copying the room's roster
        # onto it would be re-buying the cost this design exists to drop.
        parent_members, _ = await self._members.list_for_topic(room.id)
        parent_owner = next(
            (m.member_handle for m in parent_members if m.role == TopicRole.owner),
            None,
        )
        # 归属跟推进者走: WHO the child belongs to, most specific answer first.
        #
        # 1. `created_by`, when a real human called split — they said what they
        #    wanted. Both this rung and the next screen for an actual person
        #    (`names_a_person`) rather than merely "not 芝士": a 分身 splits under
        #    its OWN handle (``cheese-<topic hex>``), and `anonymous`/`system` name
        #    nobody either. Letting any of them through is not a cosmetic mistake —
        #    `seed()` refuses to make 芝士 an owner, so the room lands ownerless and
        #    nobody can manage its roster, which is the bug this ladder exists for.
        # 2. `triggered_by` — the human driving the turn 芝士 split from. A room
        #    stalls, someone else picks it up, and the work that comes out of THEIR
        #    turn is theirs: the child ends in an accept card, and handing that card
        #    to whoever opened the parent room months ago strands it a second time.
        #    GitHub credit for the original requester is not lost — they come back
        #    as `Co-authored-by:` (`workspace.identity.coauthor_handles`).
        # 3. The room's owner, then the project's — an autonomous 分身 split
        #    and a platform-initiated turn identify no person at all, and a room
        #    born before any of this has no owner to inherit, so the emptiness
        #    stops cascading down the tree here.
        project = await self._projects.get(room.project_id)
        owner_handle = (
            created_by
            if names_a_person(created_by)
            else triggered_by
            if names_a_person(triggered_by)
            else parent_owner or (project.owner_handle if project else None)
        )
        task = await TaskService(self._session).open_thread(
            project_id=room.project_id,
            room_id=room.id,
            title=title,
            owner_handle=owner_handle,
            created_by=created_by,
        )
        if paths:
            # 划出这条活要碰的地方。Refusals are NOT raised here: the caller
            # renders them alongside the warnings, and an exception would carry
            # only one of the two. A claim that was refused simply is not
            # recorded — the thread still exists and can narrow it and try again.
            await ClaimService(self._session).claim(task, paths)
        # 简报进卡, not into a document of its own — see `_seed_brief_doc` for
        # why the document could not be kept up to date. The worker gets these
        # same words a second way, in the prompt the room hands its subagent;
        # this copy is the record of what was asked for.
        task.brief = (brief or "").strip()
        await self._card_block(room, task)
        return task

    async def clone_from(
        self, *, target_topic_id: uuid.UUID, source_topic_id: uuid.UUID
    ) -> Topic:
        """Deep-copy the source topic's Claude conversation onto the target topic
        (transcript-fork, fusion-design §6 clone — distinct from 分身/split).

        分身 (dispatch_task) starts a FRESH session with a task brief; clone
        instead forks the source's full conversation state so the target resumes
        exactly where the source is."""
        target = await self.get_or_404(target_topic_id)
        source = await self.get_or_404(source_topic_id)
        if source.id == target.id:
            raise ValidationError("不能把话题克隆到它自己")
        if source.project_id != target.project_id:
            # Session dirs + workdir slugs are keyed per project; a cross-project
            # clone would point the transcript at a different repo. Keep in-project.
            raise ValidationError("只能在同一项目内克隆会话")
        # The agent working in the source is the one whose conversation this
        # forks, and the agent working in the target is the one that inherits it.
        # Both are resolved rather than assumed: a room may host several, and
        # copying one agent's transcript onto another's key would hand it a
        # thread it never had.
        sessions = AgentSessionService(self._session)
        source_agent = await self.resolve_agent(source)
        target_agent = await self.resolve_agent(target)
        source_sid = await sessions.resume_token(source.id, source_agent.handle)
        if not source_sid:
            raise ValidationError("源话题还没跑过（没有可克隆的会话）")
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
            raise ValidationError("源话题的会话记录缺失或为空，无法克隆") from exc
        # Point the target at the forked session so its next turn --resume's it.
        await sessions.remember(
            topic_id=target.id,
            agent_handle=target_agent.handle,
            resume_token=new_sid,
        )
        return target

    async def place_or_404(self, place_id: uuid.UUID) -> Place:
        """The room this id names, plus the tree it is writing to, or 404.

        The route-level counterpart of `get_or_404`, and the difference is the
        tree: nearly every handler that has a place goes on to want files.
        """
        place = await PlaceResolver(self._session).resolve(place_id)
        if place is None:
            raise NotFoundError("Topic not found")
        return place

    async def get_doc(self, topic_id: uuid.UUID) -> Block | None:
        place = await self.place_or_404(topic_id)
        return await self._blocks.doc_root(place.room_id)

    async def get_progress(
        self, topic_id: uuid.UUID
    ) -> tuple[list[dict], datetime | None]:
        """进度层 (#187): the checklist this topic's work left behind.

        Returns ``([], None)`` for a topic that never had one — an empty
        checklist and no checklist are the same thing to a reader, and making
        the caller handle a null row buys nothing.
        """
        place = await self.place_or_404(topic_id)
        row = await TopicProgressRepository(self._session).get(place.room_id)
        if row is None:
            return [], None
        return [dict(item) for item in row.items], row.updated_at

    async def edit_doc(
        self, *, topic_id: uuid.UUID, content: str, author: str, expected_version: int
    ) -> Block:
        """改文档即指令 (eval B2): upsert the topic's living doc and drop a
        '编辑了文档' event into the conversation. The agent reads the latest doc
        on its next turn, so the edit acts as an instruction.

        ``expected_version`` is the ``doc_version`` the writer read; ``0`` says
        it expects no doc to exist yet. A write based on any other version is
        refused, because this doc is only ever written whole — 芝士 setting back
        a document it assembled from a ten-minute-old copy erases whatever a
        person typed in between, with nothing left to recover it from.

        The refusal comes BEFORE any of the write's effects: no node tree, no
        '编辑了文档' event. A rejected write that still announced itself would
        put a change in the room that is not in the document.
        """
        place = await self.place_or_404(topic_id)
        topic = place.room
        # 归档后文档定格 (spec §6.3).
        if topic.status == TopicStatus.archived:
            raise ValidationError("话题已归档，文档已定格，不能再编辑")
        doc = await self._blocks.doc_root(place.room_id)
        if doc is not None:
            updated = await self._blocks.set_doc_content(
                doc, content, expected_version=expected_version
            )
            if updated is None:
                raise _doc_conflict(doc.doc_version)
            doc = updated
        else:
            if expected_version != 0:
                raise _doc_conflict(0)
            doc = await self._blocks.add(
                project_id=topic.project_id,
                topic_id=place.room_id,
                author=author,
                author_type=AuthorType.human,
                content=content,
                kind=BlockKind.doc,
            )
        # B1: also sync the structured node tree (struct_parent children) so the
        # doc's blocks get stable ids for cross-view highlight / comments later.
        await self._sync_doc_nodes(doc, content)
        # Append-only conversation event (spec H1): the doc edit is visible.
        # A human actor is emitted as the structured <@handle> token so the
        # client renders it as a clickable mention chip (resolving handle→name
        # via the roster) — NOT prose we later pattern-match. 芝士 stays plain
        # product copy: every topic's 分身 authors under its own
        # ``cheese-<topic hex>`` handle, and a raw handle is not what a reader
        # should see — one familiar name, whichever 分身 wrote it.
        actor = "芝士" if looks_like_agent_handle(author) else f"<@{author}>"
        await self._blocks.add(
            project_id=topic.project_id,
            topic_id=place.room_id,
            author=author,
            author_type=AuthorType.system,
            content=f"{actor} 编辑了文档",
            kind=BlockKind.event,
            refs=[str(doc.id)],
            # action:"doc" → the client renders the 看文档 link on this SAME
            # line — one event vocabulary for humans and 芝士 alike.
            meta={"platform": True, "action": "doc"},
        )
        return doc

    async def _sync_doc_nodes(self, root: Block, content: str) -> None:
        """Reconcile the living doc's node tree (B1) with `content` via a
        block-level diff so unchanged nodes keep their ids (anchors survive an
        edit). Re-setting the same markdown is a no-op."""
        new_nodes = markdown_to_nodes(content)
        existing = await self._blocks.list_doc_nodes(root.topic_id)
        matcher = difflib.SequenceMatcher(
            a=[b.content for b in existing],
            b=[n.content for n in new_nodes],
            autojunk=False,
        )
        # Reuse existing block ids wherever content is unchanged (equal runs).
        reuse: dict[int, Block] = {}
        for tag, i1, i2, j1, _j2 in matcher.get_opcodes():
            if tag == "equal":
                for off in range(i2 - i1):
                    reuse[j1 + off] = existing[i1 + off]
        kept_ids = {b.id for b in reuse.values()}
        for b in existing:
            if b.id not in kept_ids:
                await self._blocks.delete(b)
        for idx, node in enumerate(new_nodes):
            order = float(idx)
            block = reuse.get(idx)
            if block is not None:
                if block.struct_order != order or block.node_type != node.node_type:
                    await self._blocks.update_node(
                        block, node_type=node.node_type, struct_order=order
                    )
            else:
                await self._blocks.add(
                    project_id=root.project_id,
                    topic_id=root.topic_id,
                    author=root.author,
                    author_type=root.author_type,
                    content=node.content,
                    kind=BlockKind.doc_node,
                    struct_parent=root.id,
                    node_type=node.node_type,
                    struct_order=order,
                )

    async def add_relay_block(
        self, *, target: Task, sender: Place, label: str, text: str
    ) -> Block:
        """母子传话's message block (see `app.domain.topic.relay`).

        Lives here, not in `relay.py`, for one reason: writing a Block from
        another domain's repository is the debt `tests/unit/test_domain_import_
        guard.py` ratchets down, and this service already carries that exemption.
        Same authorship rule as `return_conclusion` below — the RECEIVING room's
        芝士 is the author, because a message from someone who is not on that
        roster reads as a ghost; `refs` links back to the sender.
        """
        author = await self._members.resolve_agent_handle(target.room_id)
        return await self._blocks.add(
            project_id=target.project_id,
            topic_id=target.room_id,
            task_id=target.id,
            author=author,
            author_type=AuthorType.ai,
            content=f"【{label}｜{sender.title}】\n{text}",
            kind=BlockKind.message,
            refs=[str(sender.room_id)],
        )
