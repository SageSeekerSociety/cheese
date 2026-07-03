"""Topic business logic, including the topic tree (spec §6, evals A1/A2/A4).

"一件事就是一个话题，话题可以长大": a block can be upgraded into its own topic
(讨论升级), a big topic can be split into sub-topics (从上往下拆解), and a
sub-topic's conclusion flows back to its parent (结论回流).
"""

import difflib
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.core.text import markdown_preview
from app.domain.block.doc_tree import markdown_to_nodes
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.notification.models import NotifKind, NotifLevel
from app.domain.notification.services import NotificationService
from app.domain.project.repositories import ProjectRepository
from app.domain.topic.models import Topic, TopicKind, TopicStatus
from app.domain.topic.repositories import TopicRepository

CHEESE_AUTHOR = "cheese"
# Titles are AI-generated (the agent names a topic via `cheese title`), never
# deterministically derived from text — see CLAUDE.md. An upgraded block starts
# untitled and 芝士 names it on its first turn (same as a + new topic).
PLACEHOLDER_TITLE = "新话题"


def _child_kind(parent: Topic) -> TopicKind:
    # A child of the root is a topic; a child of a topic/subtopic is a subtopic.
    return TopicKind.topic if parent.kind == TopicKind.root else TopicKind.subtopic


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


class TopicService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = TopicRepository(session)
        self._projects = ProjectRepository(session)
        self._blocks = BlockRepository(session)

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
            kind = _child_kind(parent)
        return await self._repo.add(
            project_id=project_id,
            title=title,
            parent_id=parent_id,
            kind=kind,
            created_by=created_by,
        )

    async def get_or_create_private(
        self, *, project_id: uuid.UUID, user_handle: str
    ) -> Topic:
        """The member's 1:1 private chat with 芝士 (spec §1)."""
        if await self._projects.get(project_id) is None:
            raise NotFoundError("Project not found")
        return await self._repo.get_or_create_private(
            project_id=project_id, user_handle=user_handle
        )

    async def get_or_404(self, topic_id: uuid.UUID) -> Topic:
        topic = await self._repo.get(topic_id)
        if topic is None:
            raise NotFoundError("Topic not found")
        return topic

    async def list_for_project(self, project_id: uuid.UUID) -> tuple[list[Topic], int]:
        return (
            await self._repo.list_for_project(project_id),
            await self._repo.count_for_project(project_id),
        )

    async def list_children(self, topic_id: uuid.UUID) -> list[Topic]:
        await self.get_or_404(topic_id)
        return await self._repo.list_children(topic_id)

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
        await self._archive_children(topic, by=by)
        await self._session.flush()
        return topic

    async def _archive_children(self, topic: Topic, *, by: str) -> None:
        children = await self._repo.list_children(topic.id)
        for child in children:
            if child.status == TopicStatus.archived:
                continue
            await self._archive_one(child, by=by, cascaded_from=topic.title)
            await self._archive_children(child, by=by)

    async def _archive_one(
        self, topic: Topic, *, by: str, cascaded_from: str | None = None
    ) -> None:
        topic.status = TopicStatus.archived
        topic.archived_at = datetime.now(UTC)
        note = (
            f"📦 随父话题「{cascaded_from}」一同归档"
            if cascaded_from
            else f"📦 {by} 归档了话题"
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
            content=f"📂 {by} 取消归档，话题恢复活跃",
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
        parent_id = parent.id
        kind = _child_kind(parent)
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
        new_topic = await self._repo.add(
            project_id=parent.project_id,
            title=title,
            parent_id=parent.id,
            kind=_child_kind(parent),
            created_by=created_by,
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
        # Display name, not raw handle — system lines read as product copy.
        display = "芝士" if author == "cheese" else author
        if author != "cheese":
            from app.domain.user.repositories import UserRepository

            user = await UserRepository(self._session).get_by_handle(author)
            if user is not None and user.name:
                display = user.name
        await self._blocks.add(
            project_id=topic.project_id,
            topic_id=topic_id,
            author=author,
            author_type=AuthorType.system,
            content=f"{display} 编辑了文档",
            kind=BlockKind.event,
            refs=[str(doc.id)],
            meta={"platform": True},
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
    ) -> Block:
        """结论回流 (spec §6.1 / eval C4): a sub-topic's (分身) conclusion flows
        back to its parent (本体) three ways — a referencing message in the
        conversation, woven into the parent's living doc (so 分身 stay consistent
        via the doc, spec §8.4), and a change-alert so the coordinator is notified.
        """
        sub = await self.get_or_404(subtopic_id)
        if sub.parent_id is None:
            raise ValidationError("Topic has no parent to return a conclusion to")
        parent = await self._repo.get(sub.parent_id)

        # 1) Conversation: a message in the parent referencing the sub-topic.
        block = await self._blocks.add(
            project_id=sub.project_id,
            topic_id=sub.parent_id,
            author=CHEESE_AUTHOR,
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
                topic_id=sub.parent_id, content=new_content, author=CHEESE_AUTHOR
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
        return block
