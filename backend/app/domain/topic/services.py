"""Topic business logic — rooms.

A room is read, spoken in, archived and cloned from here. The work dispatched
inside one is not: a block upgraded into a place of its own (讨论升级), a task
started in a room (从上往下拆解) and a task's conclusion flowing back (结论回流)
all write the `tasks` table, so they live in `app.domain.room_task` — `dispatch`
for the verbs, `archive` for closing a room's work, `place` for reading one id
as either a room or a task.

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
from app.core.errors import (
    ConflictError,
    NotFoundError,
    UnprocessableEntityError,
    ValidationError,
)
from app.core.sentences import say
from app.domain.conversation.services import room_of
from app.domain.delivery_events import SettleCard
from app.domain.event_about import EventAbout
from app.domain.session_record.tail import SessionTail, tails, working_seats
from app.domain.topic.models import (
    NotifyLevel,
    RoomCleanup,
    Topic,
    TopicKind,
    TopicProgress,
    TopicStatus,
)
from app.domain.topic.owner import resolve_owner
from app.domain.topic.ports import (
    ArchivedCards,
    CloseRoomTasks,
    ForkTranscript,
    NamedTeammate,
    PendingDecisions,
    PlatformLine,
    ProjectHarness,
    ProjectTeam,
    ReviewCards,
    RoomTeammate,
    SeatHandles,
    SessionStore,
    WorkspacePaths,
)
from app.domain.topic.repositories import (
    SortOrder,
    TopicProgressRepository,
    TopicRepository,
    TopicSortField,
    Unread,
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


def _stall_tail_summary(tail: SessionTail | None) -> dict | None:
    """What the stall verdict was read off, so a caller can check it by hand
    instead of trusting the boolean."""
    if tail is None:
        return None
    call = tail.open_call
    return {
        "agent_handle": tail.agent_handle,
        "harness": tail.harness,
        "last_at": _as_utc(tail.last_at).isoformat(),
        "open_call": None
        if call is None
        else {
            "tool": call.tool,
            "call_id": call.call_id,
            "at": _as_utc(call.at).isoformat(),
        },
    }


@dataclass(frozen=True)
class TopicRelevance:
    """What a channel is to one particular person. See ``TopicOut.joined`` /
    ``awaits_me`` for the field-level contract."""

    joined: bool = False
    awaits_me: bool = False


class TopicService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = TopicRepository(session)
        # deferred-import: breaks the cycle project.services -> topic.services
        from app.domain.project.services import ProjectService

        self._projects = ProjectService(session)
        self._members = TopicMemberService(session)

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
            raise NotFoundError(say("topicNotFound"))
        if topic.status == TopicStatus.archived:
            raise ConflictError(say("roomArchivedUnarchiveFirst"))
        return topic

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        title: str,
        description: str | None = None,
        parent_id: uuid.UUID | None = None,
        created_by: str | None = None,
        members_only: bool = False,
    ) -> Topic:
        """Open a channel, under the name whoever creates it gave it. A private
        one (``members_only``) starts with its owner as its only person."""
        title = title.strip()
        if not title:
            raise ValidationError(say("titleRequired"))
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
        number, environment = await self._projects.channel_start(project)
        topic = await self._repo.add(
            project_id=project_id,
            number=number,
            environment=environment,
            title=title,
            parent_id=parent_id,
            kind=kind,
            created_by=created_by,
        )
        topic.description = (description or "").strip() or None
        topic.members_only = members_only
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
        #
        # 芝士那一席不点名：新房间名册空空，`seed` 自己回落到项目默认的那一席
        # （`TopicMemberService._project_agent_seat`）。本包原来自己算一遍 ——
        # 走的是 `resolve_agent`，而那只为了答这一个问题往第 5 层伸手。
        await self._members.seed(
            topic.id,
            owner_handle=await resolve_owner(
                self._session,
                created_by,
                parent_id=parent_id,
                project_owner=project.owner_handle,
                project_team=project.team_id,
            ),
        )
        return topic

    async def get_or_create_private(
        self,
        *,
        project_id: uuid.UUID,
        user_handle: str,
        peer_handle: str | None = None,
        agent_handle: str | None = None,
        named_teammate: NamedTeammate,
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
            # 挑哪一位队友、它显示什么名字，是第 5 层 `agent_instance` 答的两件事
            # （`NamedTeammate`）：本包在第 2 层，手里只有项目 id。
            peer, name = await named_teammate(self._session, project_id, agent_handle)
            owner = user_handle
            title = f"与{name}私聊 · {owner}"
        topic = await self._repo.get_or_create_private(
            project_id=project_id,
            owner=owner,
            peer=peer,
            title=title,
            environment=self._projects.room_environment(project),
        )
        await TopicMemberService(self._session).seed_private(
            topic.id, owner_handle=owner, peer_handle=peer
        )
        return topic

    async def note(
        self, topic: Topic, *, by: str, content: str, events: PlatformLine
    ) -> None:
        """One platform line in a channel's main line, about something ``by``
        did there.

        ``events`` 是递进来的口子：写一条块在第 4 层（`block`），本包在第 2 层，
        而这一句「关于这个房间」的落点由那张封闭表算（`landing()`，在
        `block/adapters.py` 的实现那一侧）。
        """
        await events(
            self._session,
            about=EventAbout.room,
            project_id=topic.project_id,
            room_id=topic.id,
            author=by,
            content=content,
        )

    async def get_many(self, topic_ids: set[uuid.UUID]) -> list[Topic]:
        return await self._repo.get_many(topic_ids)

    async def get_or_404(self, topic_id: uuid.UUID) -> Topic:
        topic = await self._repo.get(topic_id)
        if topic is None:
            raise NotFoundError(say("topicNotFound"))
        return topic

    async def list_for_project(
        self,
        project_id: uuid.UUID,
        *,
        viewer: str | None,
        sort: TopicSortField | None = None,
        order: SortOrder = "asc",
        active_since: datetime | None = None,
    ) -> tuple[list[Topic], dict[uuid.UUID, datetime], int]:
        """The project's topics ``viewer`` sees, their 最后活动时间, and the
        total. A private channel is listed only to the people in it.

        Activity comes back with the rows because the query that ordered them
        already derived it; fetching it separately made the database compute
        the same correlated subquery over the same topics twice.
        """
        rows = await self._repo.list_for_project_with_activity(
            project_id,
            viewer=viewer,
            sort=sort,
            order=order,
            active_since=_as_utc(active_since),
        )
        topics = [topic for topic, _ in rows]
        last_activity = {topic.id: last for topic, last in rows}
        # `list_for_project_with_activity` takes no limit, so `rows` is already
        # the whole visible set (or the whole `active_since`-filtered set): a
        # second `count_for_project` round-trip would re-run the same predicate.
        return topics, last_activity, len(topics)

    async def last_activity_for_topics(
        self, topic_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, datetime]:
        return await self._repo.last_activity_for_topics(topic_ids)

    async def relevance_for_topics(
        self,
        topics: list[Topic],
        viewer_handle: str | None,
        *,
        team: ProjectTeam,
        cards: ReviewCards,
        decisions: PendingDecisions,
    ) -> dict[uuid.UUID, TopicRelevance]:
        """{topic_id: what it is to me} for a batch of channels, in a fixed
        number of queries whatever the batch size: the list endpoint returns a
        whole project at once, and a per-channel probe here would be a hundred
        round trips to render a sidebar.

        ``viewer_handle`` is passed IN rather than read from an auth context:
        the caller resolved the actor at the trust boundary. Anonymous/unknown
        callers relate to nothing.

        「在等我」 asks two other domains the same question at once, and asks each
        through its own口子 — the accept cards and the @-notifications each decide
        for themselves what "mine" means. 两边都在第 4 层，本包在第 2 层，所以
        答案由调用方递进来（`topic/ports.py`）。"""
        if viewer_handle is None or not topics:
            return {}
        topic_ids = [t.id for t in topics]
        if hasattr(self._members, "_repo"):
            await self._members._repo.list_for_topics(topic_ids)
        joined = await self._members.joined_topic_ids(topics, viewer_handle, team=team)
        awaiting_card = await cards.reviewer_topic_ids(topic_ids, viewer_handle)
        pending = await decisions.decision_topic_ids(topic_ids, viewer_handle)
        return {
            topic.id: TopicRelevance(
                joined=topic.id in joined,
                # 「在等我」只数要我**动手拍板**的：一张点名我还没结的验收卡，或一条
                # 还没答的决策请求。
                awaits_me=awaiting_card.get(topic.id, False)
                or pending.get(topic.id, False),
            )
            for topic in topics
        }

    async def list_children(self, topic_id: uuid.UUID) -> list[Topic]:
        await self.get_or_404(topic_id)
        return await self._repo.list_children(topic_id)

    # ---- 轮次猝死信号 (a turn can die without the process dying) ----------

    async def stall_signal(
        self,
        room_id: uuid.UUID,
        *,
        alive_for_s: float | None,
        threshold_s: float | None = None,
    ) -> dict:
        """Is a session in this room stopped in the middle of a call? A
        queryable verdict.

        The 8-hour incident (2026-08-11) was not that work died — containers
        get recreated, children get OOM-killed. It was that nothing on the
        platform could be ASKED about it: `status` still read `active`, the last
        thing the agent did was an ordinary tool call, and the only way to find
        out was for a human to notice the silence.

        Every fact read here is one the platform can check:

        1. **Its runner says the session is at work** (`session_states`). A
           session its runner declared idle, or that exited, is not doing
           anything that could be stuck, whatever its records stopped at.
        2. **Its records stop in the middle of a call.** The session wrote a
           tool call and never its result (`session_record.tail`), and has
           written nothing since for longer than the threshold.
        3. **Its runner has not answered either.** ``alive_for_s`` is how long
           since the runner holding a session in this room last answered a read
           (`realtime.broker.note_alive`), which it does every `READ_WAIT_S`
           through a long think or a long command. Heard recently ⇒ alive: a
           runner runs its own clocks on stuck work and says so. None means
           this process reads no session here: nobody is listening for it.

        The calls are read in Claude Code's record shape only, and only Claude
        Code reports its session's state: a Codex or pi session is not checked.
        """
        if threshold_s is None:
            threshold_s = settings.turn_stall_signal_s
        alive = alive_for_s is not None and alive_for_s <= threshold_s
        # The room's OWN sessions: a task's conversation is its own, and a room
        # that has gone quiet while one of its tasks works is still quiet.
        sessions = await tails(self._session, room_id)
        at_work = await working_seats(self._session, room_id)
        now = datetime.now(UTC)

        def silence(tail: SessionTail) -> int:
            return round((now - _as_utc(tail.last_at)).total_seconds())

        stopped = next(
            (
                tail
                for tail in sessions
                if (tail.agent_handle, tail.harness) in at_work
                and tail.open_call is not None
                and silence(tail) > threshold_s
            ),
            None,
        )
        latest = stopped or (sessions[0] if sessions else None)
        signal: dict = {
            "stalled": False,
            "reason": None,
            "member": None,
            "threshold_s": round(threshold_s),
            "silent_for_s": None if latest is None else silence(latest),
            "last_record": _stall_tail_summary(latest),
            "runner_answered_s": None if alive_for_s is None else round(alive_for_s),
        }
        if alive or stopped is None:
            return signal
        signal["stalled"] = True
        # Whose work stopped: the seat whose session stopped mid-call.
        signal["member"] = stopped.agent_handle
        # Which of the two ways, because they send whoever reads this to
        # different places: no process here is reading the session at all,
        # versus one that reads it and has stopped hearing from its runner.
        signal["reason"] = "not_read" if alive_for_s is None else "runner_silent"
        return signal

    # ---- 话题级未读 (Feishu-style badges) -------------------------------

    async def unread_counts(
        self, project_id: uuid.UUID, user_handle: str
    ) -> dict[uuid.UUID, Unread]:
        if await self._projects.get(project_id) is None:
            raise NotFoundError("Project not found")
        return await self._repo.unread_counts(project_id, user_handle)

    async def private_unread_counts(
        self, project_id: uuid.UUID, user_handle: str, *, seat_handles: SeatHandles
    ) -> dict[str, int]:
        project = await self._projects.get(project_id)
        if project is None:
            raise NotFoundError("Project not found")
        seats = await seat_handles(self._session, project_id)
        return await self._repo.private_unread_counts(
            project_id,
            user_handle,
            seats=seats,
            default_agent_instance_id=project.default_agent_instance_id,
        )

    # ---- 有人回复了我、我还没处理（侧栏那盏黄灯） -----------------------

    async def replied_to_me(
        self, room_ids: list[uuid.UUID], user_handle: str
    ) -> set[uuid.UUID]:
        """Which of these channels hold an answer to this person that they have
        not opened the channel since — 回复了我发的消息，或者 @ 我
        (`TopicRepository.replied_to_me`)."""
        return await self._repo.replied_to_me(room_ids, user_handle)

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

    async def notify_levels(
        self, project_id: uuid.UUID, user_handle: str
    ) -> dict[uuid.UUID, tuple[NotifyLevel, datetime | None]]:
        if await self._projects.get(project_id) is None:
            raise NotFoundError("Project not found")
        return await self._repo.notify_levels(project_id, user_handle)

    async def set_notify_level(
        self,
        topic_id: uuid.UUID,
        user_handle: str,
        level: str,
        muted_until: datetime | None = None,
    ) -> None:
        """Set how much of a channel reaches this person. A mute may end at a
        time, which has to be still to come."""
        if level not in NotifyLevel.__members__:
            raise ValidationError(say("topicNotifyLevelInvalid"))
        if muted_until is not None and muted_until <= datetime.now(UTC):
            raise ValidationError(say("topicMuteUntilPast"))
        await self.get_or_404(topic_id)
        await self._repo.set_notify_level(
            topic_id, user_handle, NotifyLevel(level), muted_until
        )

    # ---- 手动归档 / 取消归档 (归档去向, spec §6.3 extension) -------------

    async def archive(
        self,
        topic_id: uuid.UUID,
        *,
        by: str,
        settle_card: SettleCard,
        cards: ArchivedCards,
        close_tasks: CloseRoomTasks,
        events: PlatformLine,
    ) -> Topic:
        """A person archives a channel (idempotent): it leaves everyone's
        sidebar and is read, not spoken in, until someone unarchives it. A
        channel still holding open tasks is refused: archiving it would close
        work its owners are still doing, and that is theirs to close or move.
        综合 is never archived."""
        topic = await self._repo.lock(topic_id)
        if topic is None:
            raise NotFoundError(say("topicNotFound"))
        if topic.kind == TopicKind.root:
            raise ValidationError(say("projectRootCannotArchive"))
        if topic.status == TopicStatus.archived:
            return topic
        if await self._repo.has_open_tasks(topic.id):
            raise ValidationError(say("channelArchiveOpenTasks"))
        await self._archive_one(
            topic, by=by, settle_card=settle_card, cards=cards, events=events
        )
        await self._archive_children(
            topic,
            by=by,
            settle_card=settle_card,
            cards=cards,
            close_tasks=close_tasks,
            events=events,
        )
        await self._session.flush()
        return topic

    async def _archive_children(
        self,
        topic: Topic,
        *,
        by: str,
        settle_card: SettleCard | None,
        cards: ArchivedCards,
        close_tasks: CloseRoomTasks,
        events: PlatformLine,
    ) -> None:
        """Closing a place closes what is inside it.

        Two kinds of inside, and they are not the same thing. Rooms nest one
        level under the root, so the recursion below still applies there. Threads
        do not nest and are not `topics` rows at all — closing the room closes
        them because a piece of work with no room has no context and no way back,
        and the cards they are still waiting on have to be settled with them
        (an unresolved card on a frozen place is one nobody can ever act on).
        """
        # 活那一行由上面那句口子改成结束（`CloseRoomTasks`）：`tasks` 是本包向上的
        # 那张表里的行，结束一件活是那个域的判断，不是本包的。本包说的是**为什么**它
        # 结束了 —— 那句话要落在每条活自己的支线里（`PlatformLine` 写下去），房间
        # 自己那条归档的话落在房间上，而后来的谁打开这条支线，看到的是一句说到一半
        # 就没了的活。
        for thread_id in await close_tasks(self._session, topic.id):
            await events(
                self._session,
                about=EventAbout.task,
                project_id=topic.project_id,
                room_id=topic.id,
                task_id=thread_id,
                author=by,
                content=say("threadArchivedWithRoom", room=topic.title),
            )
        # 卡上还开着的验收卡由 `_archive_one` 一并收：它们和房间自己的卡挂在同一
        # 个房间上，一次问清比每条活问一次少一半的往返，也不会漏掉一条活被收工
        # 之后才归档的房间。
        children = await self._repo.list_children(topic.id)
        for child in children:
            if child.status == TopicStatus.archived:
                continue
            await self._archive_one(
                child,
                by=by,
                cascaded_from=topic.title,
                settle_card=settle_card,
                cards=cards,
                events=events,
            )
            await self._archive_children(
                child,
                by=by,
                settle_card=settle_card,
                cards=cards,
                close_tasks=close_tasks,
                events=events,
            )

    async def _overview_room(self, project_id: uuid.UUID) -> uuid.UUID:
        """项目总览那个房间（`TopicKind.root`）——项目的事落在这里。

        总览是哪个房间只有项目行知道，`landing()` 读不到它，所以这一句由调用点
        负责：从 `Project.root_topic_id` 取，不自己挑一个房间。
        """
        project = await self._projects.get(project_id)
        assert project is not None  # 房间的 project_id 是外键，项目行必在。
        assert project.root_topic_id is not None  # create() 一定播种了总览。
        return project.root_topic_id

    async def _where_said(self, topic: Topic) -> uuid.UUID:
        """Where what happens to a channel is said: the project overview, which
        everyone in the project reads — except for a private channel, whose
        name and work nobody outside it sees, so it is said there."""
        if topic.members_only:
            return topic.id
        return await self._overview_room(topic.project_id)

    async def _archive_one(
        self,
        topic: Topic,
        *,
        by: str,
        settle_card: SettleCard | None,
        cards: ArchivedCards,
        events: PlatformLine,
        cascaded_from: str | None = None,
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

        overview_room = await self._where_said(topic)
        await cards(
            self._session,
            topic_id=topic.id,
            project_id=topic.project_id,
            topic_title=topic.title,
            overview_room_id=overview_room,
            by=by,
            settle_card=settle_card,
        )
        note = (
            say("roomArchivedWithParent", room=topic.title, parent=cascaded_from)
            if cascaded_from
            else say("roomArchived", actor=f"<@{by}>", room=topic.title)
        )
        # 房间归档是项目的事，不是这个房间的事（结论 14）：房间关掉之后没人再打开
        # 它的时间线，而「少了一个房间」恰恰是项目总览要记的一行。
        await events(
            self._session,
            about=EventAbout.project,
            project_id=topic.project_id,
            room_id=overview_room,
            author=by,
            content=note,
        )

    async def unarchive(
        self,
        topic_id: uuid.UUID,
        *,
        by: str,
        sessions: SessionStore,
        events: PlatformLine,
    ) -> Topic:
        """Bring an archived topic back to active (idempotent). Accept markers
        are kept — un-archiving doesn't rewrite acceptance history (use 撤回采纳
        for that).

        ``sessions`` 是递进来的口子：一间房回到项目里，它那台工作机还在原地
        （如果停到一半被按住了），得把记着的那张令牌忘掉，让下一轮重新开一张。
        会话在第 5 层，本包在第 2 层。"""
        topic = await self._repo.lock(topic_id)
        if topic is None:
            raise NotFoundError(say("topicNotFound"))
        if topic.deleted_at is not None:
            raise UnprocessableEntityError(say("channelDeletedCannotUnarchive"))
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
                await sessions.forget_room(topic.id)
        topic.status = TopicStatus.active
        topic.archived_at = None
        topic.cleanup_due_at = None
        topic.cleanup_id = None
        # 归档落总览，取消归档就落在同一条线上：一个房间回到项目里，和它离开项目
        # 是同一件事的两面，读的人也在同一个地方读。
        await events(
            self._session,
            about=EventAbout.project,
            project_id=topic.project_id,
            room_id=await self._where_said(topic),
            author=by,
            content=say("roomUnarchived", actor=f"<@{by}>", room=topic.title),
        )
        await self._session.flush()
        return topic

    # ---- 删除 (归档的去向，清单第 2 条) ---------------------------------

    async def delete(
        self,
        topic_id: uuid.UUID,
        *,
        by: str,
        settle_card: SettleCard,
        cards: ArchivedCards,
        close_tasks: CloseRoomTasks,
        events: PlatformLine,
    ) -> None:
        """A person deletes a channel: it leaves every list for good, and what
        was said in it, the tasks in it and its documents go with it.

        Deleting is archiving that does not come back, and it goes the same way
        (``_archive_one``): the channel's session is stopped, its cards are
        settled, its machines are freed. The row goes when that cleanup
        finishes (``topic/retire.py``); until then the channel is an archived
        row with ``deleted_at`` set — off every list, and 取消归档 is refused
        for it. 综合 is never deleted, and a channel still holding open tasks
        is refused, for the same reason archiving it is idempotent: no new work
        in a channel that is leaving.
        """
        topic = await self._repo.lock(topic_id)
        if topic is None:
            raise NotFoundError(say("topicNotFound"))
        if topic.kind == TopicKind.root:
            raise UnprocessableEntityError(say("projectRootCannotDelete"))
        if topic.deleted_at is not None:
            return
        if topic.status != TopicStatus.archived:
            if await self._repo.has_open_tasks(topic.id):
                raise UnprocessableEntityError(say("channelArchiveOpenTasks"))
            await self._archive_one(
                topic, by=by, settle_card=settle_card, cards=cards, events=events
            )
            await self._archive_children(
                topic,
                by=by,
                settle_card=settle_card,
                cards=cards,
                close_tasks=close_tasks,
                events=events,
            )
        deleted_at = datetime.now(UTC)
        topic.deleted_at = deleted_at
        # 归档把清理排在宽限之后；删除就排在现在 —— 删掉就是删掉，不必等一天。
        topic.cleanup_due_at = deleted_at
        operation = (
            await self._session.get(RoomCleanup, topic.cleanup_id)
            if topic.cleanup_id
            else None
        )
        if operation is None or operation.state == "complete":
            # 没有清理会再来了（早归档的频道，或者归档时它就没排上）：这一行现在
            # 就交出去删掉，不然它会永远躺着。
            await self._session.delete(topic)
        else:
            operation.due_at = deleted_at
        await self._session.flush()

    async def archive_with_project(
        self,
        project_id: uuid.UUID,
        *,
        by: str,
        settle_card: SettleCard | None,
        cards: ArchivedCards,
        close_tasks: CloseRoomTasks,
        events: PlatformLine,
    ) -> None:
        """Archive every room of a project being archived — the overview and the
        private chats too, which nobody can archive one at a time.

        ``settle_card`` 可以是 ``None``：这条路的门是 `project.archive`，它能构造出
        一个没递口子的服务（见 `project/services.py`），而这里不替它挡 —— ``None``
        会一路走到 `review.archive`，由那里当场说清是哪个门漏了。

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
                await self._archive_one(
                    topic, by=by, settle_card=settle_card, cards=cards, events=events
                )
                await self._archive_children(
                    topic,
                    by=by,
                    settle_card=settle_card,
                    cards=cards,
                    close_tasks=close_tasks,
                    events=events,
                )
            topic.archived_with_project = True
        await self._session.flush()

    async def unarchive_with_project(
        self,
        project_id: uuid.UUID,
        *,
        by: str,
        sessions: SessionStore,
        events: PlatformLine,
    ) -> None:
        """Bring back the rooms :meth:`archive_with_project` took along."""
        for topic in await self._repo.lock_all_in_project(project_id):
            if not topic.archived_with_project:
                continue
            await self.unarchive(topic.id, by=by, sessions=sessions, events=events)
            topic.archived_with_project = False
        await self._session.flush()

    async def clone_from(
        self,
        *,
        target_topic_id: uuid.UUID,
        source_topic_id: uuid.UUID,
        room_teammate: RoomTeammate,
        fork: ForkTranscript,
        project_harness: ProjectHarness,
        paths: WorkspacePaths,
        sessions: SessionStore,
    ) -> Topic:
        """Deep-copy the source topic's Claude conversation onto the target topic
        (transcript-fork, fusion-design §6 clone — distinct from a task).

        A task (create_task) starts a FRESH session of its own; clone instead
        forks the source's full conversation state so the target resumes
        exactly where the source is.

        ``room_teammate``、``fork``、``project_harness`` 三样都是递进来的口子：两边
        各是谁在干活（`RoomTeammate`）、转写怎么拷（`ForkTranscript`）、这个项目跑
        哪个骨架（`ProjectHarness`），答案都在第 5 层，本包在第 2 层。``paths`` 和
        ``sessions`` 同理：转写和沙箱目录落在磁盘的哪一格（`WorkspacePaths`），
        一个房间跑着的会话记在哪（`SessionStore`）。"""
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
        _, source_handle = await room_teammate(self._session, source.id)
        _, target_handle = await room_teammate(self._session, target.id)
        # 骨架不是参与者的属性（结论 28），是项目设置加部署设置答的一件事。源和
        # 目标同属一个项目（上面已经拒了跨项目），所以两边解析出来的是同一个，
        # 「两边骨架不同」这个问题在这里不存在。
        owner = await self._projects.get(target.project_id)
        harness = project_harness(owner.settings if owner else None)
        source_sid = await sessions.resume_token(
            source.id, source_handle, harness=harness
        )
        if not source_sid:
            raise ValidationError(say("topicCloneNeverRan"))
        try:
            new_sid = fork(
                source_session_dir=paths.session_dir(source.project_id, source.id),
                source_session_id=source_sid,
                target_session_dir=paths.session_dir(target.project_id, target.id),
                # Claude resolves --resume under the slug of the cwd it runs
                # with, so the fork must land under the TARGET topic's workdir.
                target_cwd=paths.sandbox_topic_workdir(target.id),
            )
        except FileNotFoundError as exc:
            raise ValidationError(say("topicCloneRecordMissing")) from exc
        # Point the target at the forked session so its next turn --resume's it.
        await sessions.remember(
            conversation_id=target.id,
            agent_handle=target_handle,
            resume_token=new_sid,
            harness=harness,
        )
        return target

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

    async def save_progress(
        self,
        conversation_id: uuid.UUID,
        items: list[dict],
        *,
        turn_id: uuid.UUID | None = None,
    ) -> TopicProgress:
        """进度层 (#187) 的写侧: overwrite this topic's checklist.

        The write counterpart of `get_progress`, served here so a route no
        longer reaches ``TopicProgressRepository`` itself for the one row it
        writes.
        """
        return await TopicProgressRepository(self._session).save(
            conversation_id, items, turn_id=turn_id
        )

    async def names_in_projects(
        self, project_ids: list[uuid.UUID], *, viewer: str
    ) -> list[Topic]:
        """These projects' rooms, named, as ``viewer`` may see them.

        ``GET /topics/names`` — the command palette's cross-project room
        search. The route used to build ``TopicRepository`` for it; the query
        and its ordering ("最后活动时间 desc") are unchanged, only the caller is.
        """
        return await self._repo.names_in_projects(project_ids, viewer=viewer)

    async def cleanup_operation(self, topic: Topic) -> RoomCleanup | None:
        """The room's scheduled cleanup, or None when it has none.

        ``RoomCleanup`` lives on this side of the boundary, so the route asks
        the service for the row instead of naming the model in a ``db.get``.
        """
        if not topic.cleanup_id:
            return None
        return await self._session.get(RoomCleanup, topic.cleanup_id)
