"""Topic business logic, including the topic tree (spec §6, evals A1/A2/A4).

"一件事就是一个话题，话题可以长大": a block can be upgraded into its own topic
(讨论升级), a big topic can be split into sub-topics (从上往下拆解), and a
sub-topic's conclusion flows back to its parent (结论回流).
"""

import difflib
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import NotFoundError, ValidationError
from app.core.text import markdown_preview
from app.domain.agent import clone
from app.domain.block.doc_tree import markdown_to_nodes
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.conclusion.models import ConclusionCard
from app.domain.conclusion.services import ConclusionCardService
from app.domain.cx_notification.models import NotifKind, NotifLevel
from app.domain.cx_notification.services import NotificationService
from app.domain.identity.handles import looks_like_agent_handle
from app.domain.project.repositories import ProjectRepository
from app.domain.topic.models import Topic, TopicKind, TopicRole, TopicStatus
from app.domain.topic.repositories import SortOrder, TopicRepository, TopicSortField
from app.domain.topic_membership.services import TopicMemberService
from app.domain.workspace import service as ws

# Titles are AI-generated (the agent names a topic via `cheese title`), never
# deterministically derived from text — see CLAUDE.md. An upgraded block starts
# untitled and 芝士 names it on its first turn (same as a + new topic).
PLACEHOLDER_TITLE = "新话题"


def _child_kind(parent: Topic) -> TopicKind:
    """A child of the root is a room; anything below a room is a piece of work.

    Rooms nest exactly one level. Work does not nest at all — a task cannot be
    split into sub-tasks, which is the same call Claude Code's agent teams make
    (a teammate cannot spawn teammates). Splitting work further means another
    task in the same room, not a deeper tree.

    Enforcing that is `_placement`'s job, not this function's: this only names
    what a child of a given kind IS.
    """
    return TopicKind.topic if parent.kind == TopicKind.root else TopicKind.task


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
        "## 父话题当时的活文档（快照，供参考）",
        parent_doc.strip()
        if parent_doc and parent_doc.strip()
        else "（父话题当时还没有活文档）",
    ]
    return "\n\n".join(parts)


def _as_utc(moment: datetime) -> datetime:
    """The column is TIMESTAMPTZ, but some drivers hand back a naive value and a
    naive one raises rather than merely reading wrong when subtracted."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


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


class TopicService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = TopicRepository(session)
        self._projects = ProjectRepository(session)
        self._blocks = BlockRepository(session)
        self._members = TopicMemberService(session)

    async def _placement(self, parent: Topic) -> tuple[uuid.UUID, TopicKind]:
        """Where a new child actually lands in the tree, and what it is.

        `_child_kind` says a child of a room is work; this says work has no
        children. A split requested from INSIDE a task (a 分身 that finds its
        job needs to be two jobs) becomes another task in the same room, placed
        beside its requester instead of below it — the tree stays 本体 > 房间 >
        事, and the room keeps listing every piece of work it holds.

        Nothing else about the request changes: the requesting task still
        supplies the roster and the doc snapshot the child is born with, since
        that is the context the work actually came from.
        """
        if parent.kind in (TopicKind.task, TopicKind.subtopic):
            room_id = parent.parent_id
            if room_id is None:
                # A parentless task shouldn't exist; if one does, 本体 holds the
                # sibling rather than letting the tree grow a second level here.
                project = await self._projects.get(parent.project_id)
                room_id = (project.root_topic_id if project else None) or parent.id
            return room_id, TopicKind.task
        return parent.id, _child_kind(parent)

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        title: str,
        parent_id: uuid.UUID | None = None,
        created_by: str | None = None,
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
            parent_id, kind = await self._placement(parent)
        topic = await self._repo.add(
            project_id=project_id,
            title=title,
            parent_id=parent_id,
            kind=kind,
            created_by=created_by,
        )
        # 群聊房间的地基 (fusion-design §3): seed the roster — creator = owner,
        # 芝士 joins as a member. `created_by` alone is not enough: 芝士 itself
        # creating a topic, or a caller whose token didn't resolve (anonymous
        # Phase-0), would leave the room OWNERLESS — seed() deliberately skips
        # "cheese"/None as owner — and then nobody can manage its roster, and
        # every sub-topic split beneath it inherits the same emptiness
        # (split_to_subtopic falls back to the PARENT's owner). Same fallback
        # ladder as split_to_subtopic, one level wider.
        await self._members.seed(
            topic.id,
            owner_handle=await self._resolve_owner(
                created_by, parent_id=parent_id, project_owner=project.owner_handle
            ),
        )
        return topic

    async def _resolve_owner(
        self,
        created_by: str | None,
        *,
        parent_id: uuid.UUID | None,
        project_owner: str | None,
    ) -> str | None:
        """Who owns a newborn topic: the real human who created it, else the
        parent room's owner, else the project's owner. Returns None only when
        the whole chain is ownerless (a legacy project) — the caller still
        seeds 芝士, and the room stays manageable by any project member.

        "Is the creator 芝士" spans the whole agent handle namespace, not the bare
        ``cheese`` string: each 分身 creates under its own ``cheese-<topic hex>``
        handle, and a string match would make the 分身 the room's owner — the one
        thing this chain exists to prevent (same rule as split_to_subtopic)."""
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
        return project_owner

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
        return await self._repo.get_or_create_private(
            project_id=project_id, user_handle=user_handle, peer_handle=peer_handle
        )

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
    ) -> tuple[list[Topic], int]:
        return (
            await self._repo.list_for_project(project_id, sort=sort, order=order),
            await self._repo.count_for_project(project_id),
        )

    async def list_children(self, topic_id: uuid.UUID) -> list[Topic]:
        await self.get_or_404(topic_id)
        return await self._repo.list_children(topic_id)

    # ---- 轮次猝死信号 (a turn can die without the process dying) ----------

    async def stall_signal(
        self,
        topic_id: uuid.UUID,
        *,
        live_turn: dict | None,
        background_tasks: int = 0,
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
           sees). Present and recently framed ⇒ alive, full stop. Registered
           background commands (`cheese await`) count the same way: the agent
           declared it is waiting on something long, so silence is expected.
        2. **The timeline stops mid-action.** The newest block is a tool action
           by 芝士 — the shape a turn leaves when it is cut off between doing
           something and reporting it. A turn that ends properly leaves a
           message, and every failure path the platform knows about leaves a
           system event; neither counts as stalled, because in both cases the
           topic already says what happened.
        """
        await self.get_or_404(topic_id)
        if threshold_s is None:
            threshold_s = settings.turn_stall_signal_s
        heartbeat_s = None if live_turn is None else live_turn.get("silent_for_s")
        alive = live_turn is not None and (
            heartbeat_s is not None and heartbeat_s <= threshold_s
        )
        last = await self._blocks.latest_for_topic(topic_id)
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
            "background_tasks": background_tasks,
        }
        if alive or background_tasks > 0:
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
            return topic
        await self._archive_one(topic, by=by)
        await self.archive_children(topic, by=by)
        await self._session.flush()
        return topic

    async def archive_children(self, topic: Topic, *, by: str) -> list[Topic]:
        """Archive a topic's still-active descendants; returns the ones it took.

        Public because 采纳即归档 needs the same cascade (review/services.py):
        accepting a room used to archive only the room and leave the work inside
        it running — active tasks with no parent context and no way back, the
        exact orphans this cascade exists to prevent.
        """
        taken: list[Topic] = []
        children = await self._repo.list_children(topic.id)
        for child in children:
            if child.status == TopicStatus.archived:
                continue
            await self._archive_one(child, by=by, cascaded_from=topic.title)
            taken.append(child)
            taken += await self.archive_children(child, by=by)
        return taken

    async def _archive_one(
        self, topic: Topic, *, by: str, cascaded_from: str | None = None
    ) -> None:
        topic.status = TopicStatus.archived
        topic.archived_at = datetime.now(UTC)
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
            f"📦 随父话题「{cascaded_from}」一同归档"
            if cascaded_from
            else f"📦 <@{by}> 归档了话题"
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
            content=f"📂 <@{by}> 取消归档，话题恢复活跃",
            kind=BlockKind.event,
            meta={"platform": True},
        )
        await self._session.flush()
        return topic

    async def upgrade_block_to_topic(
        self, *, block_id: uuid.UUID, created_by: str | None = None
    ) -> tuple[Topic, bool]:
        """讨论升级 (eval A1): turn a block into its own topic; the original
        position becomes a live link. The upgraded block itself is the task
        statement, preset (with a parent-doc snapshot) as the new topic's
        living doc; the 分身's auto-kickoff writes its own opening — same
        mechanics as split, no canned template.

        Returns (topic, created): created=False on an idempotent re-upgrade,
        so the caller doesn't kick the 分身 off twice."""
        block = await self._blocks.get(block_id)
        if block is None:
            raise NotFoundError("Block not found")
        # Idempotent: a second 升级 on the same block just returns the topic it
        # already created (so a double-click navigates instead of erroring).
        if block.upgraded_to_topic_id is not None:
            existing = await self._repo.get(block.upgraded_to_topic_id)
            if existing is not None:
                return existing, False
        parent = await self._repo.get(block.topic_id)
        if parent is None:
            raise NotFoundError("Parent topic not found")
        # 归档后工作面冻结 (spec §6.3) — consistent with split/edit_doc.
        if parent.status == TopicStatus.archived:
            raise ValidationError("话题已归档（工作面冻结），请从结论升级成新话题")

        # 私聊不是话题树的父节点 (spec §1): upgrading a block out of a private
        # chat lands a real topic under the project root, not under the chat
        # (which list_for_project hides → would be an invisible orphan).
        parent_id, kind = await self._placement(parent)
        if parent.is_private:
            project = await self._projects.get(block.project_id)
            root_id = project.root_topic_id if project else None
            if root_id is not None:
                parent_id = root_id
                kind = TopicKind.topic

        new_topic = await self._repo.add(
            project_id=block.project_id,
            title=PLACEHOLDER_TITLE,
            parent_id=parent_id,
            kind=kind,
            created_by=created_by,
            upgraded_from_block_id=block.id,
        )
        await self._members.seed(new_topic.id, owner_handle=created_by)
        await self._blocks.set_upgraded_to_topic(block, new_topic.id)
        # Parent doc snapshot only from a real topic — never copy a private
        # chat's doc into a public topic.
        parent_doc = (
            None if parent.is_private else await self._blocks.doc_root(parent.id)
        )
        await self._seed_brief_doc(
            new_topic,
            _brief_doc(
                child_title=PLACEHOLDER_TITLE,
                parent_title=parent.title,
                brief=None,
                parent_doc=parent_doc.content if parent_doc else None,
                created_by=created_by,
                source_block=block.content,
            ),
        )
        return new_topic, True

    async def _seed_brief_doc(self, topic: Topic, content: str) -> None:
        """Preset a newborn topic's living doc with its task brief. Author is
        `system`: the platform assembled it from existing text — nothing here
        speaks as 芝士 (the 分身's kickoff turn writes the real opening)."""
        doc = await self._blocks.add(
            project_id=topic.project_id,
            topic_id=topic.id,
            author="system",
            author_type=AuthorType.system,
            content=content,
            kind=BlockKind.doc,
        )
        await self._sync_doc_nodes(doc, content)

    async def split_to_subtopic(
        self,
        *,
        parent_topic_id: uuid.UUID,
        title: str,
        created_by: str | None = None,
        brief: str | None = None,
    ) -> Topic:
        """从上往下拆解 (eval A2): split a todo into a sub-topic under a topic.

        The child is born with a TASK BRIEF as its living doc (分身靠文档保持
        一致, spec §8.4): the splitter's `brief` plus a verbatim snapshot of the
        parent's living doc. No canned opening message anymore — the 分身's
        auto-kickoff first turn (routes/topics.py) writes its own opening
        (复述确认), because 语义内容必须由 AI 生成 (see CLAUDE.md)."""
        parent = await self.get_or_404(parent_topic_id)
        # 归档后工作面冻结 (spec §6.3): no new sub-topics under a frozen topic —
        # follow-up work starts a new topic from the conclusion (升级), not here.
        if parent.status == TopicStatus.archived:
            raise ValidationError("话题已归档（工作面冻结），请从结论升级成新话题")
        # 拆 from inside a task lands the new task BESIDE it in the same room
        # (work doesn't nest) — the roster and doc snapshot below still come
        # from `parent`, the task this work was actually split out of.
        placed_under, kind = await self._placement(parent)
        new_topic = await self._repo.add(
            project_id=parent.project_id,
            title=title,
            parent_id=placed_under,
            kind=kind,
            created_by=created_by,
        )
        # Inherit the parent's roster (not just the requested owner): a 分身-
        # initiated split otherwise leaves every human off the child's roster
        # (owner_handle="cheese" is skipped by seed()), which is the bug this
        # whole path exists to close — see seed_split's docstring.
        parent_members, _ = await self._members.list_for_topic(parent.id)
        parent_owner = next(
            (m.member_handle for m in parent_members if m.role == TopicRole.owner),
            None,
        )
        # A split requested BY a real human keeps them as the child's owner. But
        # when the splitter is 芝士 itself (e.g. an autonomous 分身发起的拆分) or no
        # human is identified at all, `created_by` alone would leave the child
        # ownerless (seed() intentionally skips 芝士 as owner) — nobody could then
        # manage its roster. Two independent gaps, both closed here:
        #
        # 1. WHO counts as 芝士: the check spans the whole 芝士 handle namespace,
        #    because 分身 split under their OWN handle (``cheese-<topic hex>``) and
        #    matching the bare ``cheese`` string would hand them the very
        #    ownership this branch exists to withhold.
        # 2. WHERE the fallback lands: the parent's real human owner, and when the
        #    parent is itself ownerless (a room born before this fallback existed),
        #    the project's owner — so the emptiness stops cascading down the tree.
        project = await self._projects.get(parent.project_id)
        owner_handle = (
            created_by
            if created_by and not looks_like_agent_handle(created_by)
            else parent_owner or (project.owner_handle if project else None)
        )
        await self._members.seed_split(
            new_topic.id,
            owner_handle=owner_handle,
            member_handles=[m.member_handle for m in parent_members],
        )
        parent_doc = await self._blocks.doc_root(parent.id)
        await self._seed_brief_doc(
            new_topic,
            _brief_doc(
                child_title=title,
                parent_title=parent.title,
                brief=brief,
                parent_doc=parent_doc.content if parent_doc else None,
                created_by=created_by,
            ),
        )
        return new_topic

    async def clone_from(
        self, *, target_topic_id: uuid.UUID, source_topic_id: uuid.UUID
    ) -> Topic:
        """Deep-copy the source topic's Claude conversation onto the target topic
        (transcript-fork, fusion-design §6 clone — distinct from 分身/split).

        分身 (split_to_subtopic) starts a FRESH session with a task brief; clone
        instead forks the source's full conversation state so the target resumes
        exactly where the source is. Only the interactive backends (tmux/device)
        keep a real Claude session file on disk; the sdk backend has none, so
        clone is unsupported there (start a fresh 子话题 instead — a graceful
        degrade, not a silent no-op)."""
        if settings.agent_backend not in ("tmux", "device"):
            raise ValidationError(
                "当前后端没有独立会话文件，无法克隆会话；请改用「拆子话题」新起会话"
            )
        target = await self.get_or_404(target_topic_id)
        source = await self.get_or_404(source_topic_id)
        if source.id == target.id:
            raise ValidationError("不能把话题克隆到它自己")
        if source.project_id != target.project_id:
            # Session dirs + the /work slug are keyed per project; a cross-project
            # clone would point the transcript at a different repo. Keep in-project.
            raise ValidationError("只能在同一项目内克隆会话")
        source_sid = source.session_id
        if not source_sid:
            raise ValidationError("源话题还没跑过（没有可克隆的会话）")
        new_sid = clone.mint_session_id()
        try:
            clone.clone_transcript_files(
                source_session_dir=ws.session_dir(source.project_id, source.id),
                source_session_id=source_sid,
                target_session_dir=ws.session_dir(target.project_id, target.id),
                new_session_id=new_sid,
            )
        except FileNotFoundError as exc:
            raise ValidationError("源话题的会话记录缺失或为空，无法克隆") from exc
        # Point the target at the forked session so its next turn --resume's it.
        await self._repo.set_session_id(target, new_sid)
        return target

    async def get_doc(self, topic_id: uuid.UUID) -> Block | None:
        await self.get_or_404(topic_id)
        return await self._blocks.doc_root(topic_id)

    async def edit_doc(
        self, *, topic_id: uuid.UUID, content: str, author: str
    ) -> Block:
        """改文档即指令 (eval B2): upsert the topic's living doc and drop a
        '编辑了文档' event into the conversation. The agent reads the latest doc
        on its next turn, so the edit acts as an instruction."""
        topic = await self.get_or_404(topic_id)
        # 归档后文档定格 (spec §6.3): a frozen topic's doc is read-only.
        if topic.status == TopicStatus.archived:
            raise ValidationError("话题已归档，文档已定格，不能再编辑")
        doc = await self._blocks.doc_root(topic_id)
        if doc is not None:
            doc = await self._blocks.update_content(doc, content)
        else:
            doc = await self._blocks.add(
                project_id=topic.project_id,
                topic_id=topic_id,
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
            topic_id=topic_id,
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

    async def return_conclusion(
        self, *, subtopic_id: uuid.UUID, conclusion: str
    ) -> tuple[Block, ConclusionCard | None]:
        """结论回流 (spec §6.1 / eval C4): a sub-topic's (分身) conclusion flows
        back to its parent (本体) three ways — a referencing message in the
        conversation, woven into the parent's living doc (so 分身 stay consistent
        via the doc, spec §8.4), and a change-alert so the coordinator is notified.

        结论卡·阶段一 (purely additive): a 4th thing now happens — an ``open``
        conclusion card is filed for the parent to settle, which is what finally
        gives 回流 a receipt, a status and idempotency. The three side effects
        above are UNCHANGED; nothing about the old flow depends on the card.
        """
        sub = await self.get_or_404(subtopic_id)
        if sub.parent_id is None:
            raise ValidationError("Topic has no parent to return a conclusion to")
        parent = await self._repo.get(sub.parent_id)

        # 1) Conversation: a message in the parent referencing the sub-topic.
        # Authored by the PARENT room's 芝士 — the conclusion lands in that room,
        # and a message from someone who is not in it reads as a ghost.
        parent_agent = await self._members.resolve_agent_handle(sub.parent_id)
        block = await self._blocks.add(
            project_id=sub.project_id,
            topic_id=sub.parent_id,
            author=parent_agent,
            author_type=AuthorType.ai,
            content=f"【子话题结论｜{sub.title}】\n{conclusion}",
            kind=BlockKind.message,
            refs=[str(sub.id)],
        )

        # 2) Living doc: append the conclusion as a section (unless frozen, §6.3).
        if parent is not None and parent.status != TopicStatus.archived:
            root = await self._blocks.doc_root(sub.parent_id)
            section = f"## 子话题结论：{sub.title}\n{conclusion}"
            existing = root.content.strip() if root and root.content else ""
            new_content = f"{existing}\n\n{section}" if existing else section
            await self.edit_doc(
                topic_id=sub.parent_id, content=new_content, author=parent_agent
            )

        # 3) Notify 本体 (the coordinator) that the 分身 finished.
        await NotificationService(self._session).create(
            project_id=sub.project_id,
            level=NotifLevel.light,
            kind=NotifKind.change_alert,
            title=f"子话题「{sub.title}」已完成",
            body=markdown_preview(conclusion, 200),
            topic_id=sub.parent_id,
        )

        # 4) 结论卡: the receipt. Opening it can't fail the 回流 — a parent that
        # was already archived has no turn left to settle a card, so it gets the
        # three side effects above and no card.
        card = None
        if parent is not None and parent.status != TopicStatus.archived:
            card = await ConclusionCardService(self._session).open_for_conclusion(
                sub=sub, parent=parent, conclusion=conclusion
            )
        return block, card
