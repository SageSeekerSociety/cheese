"""Topic business logic, including the topic tree (spec §6, evals A1/A2/A4).

"一件事就是一个话题，话题可以长大": a block can be upgraded into its own topic
(讨论升级), a big topic can be split into sub-topics (从上往下拆解), and a
sub-topic's conclusion flows back to its parent (结论回流).
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.project.repositories import ProjectRepository
from app.domain.topic.models import Topic, TopicKind, TopicStatus
from app.domain.topic.repositories import TopicRepository

CHEESE_AUTHOR = "cheese"
_TITLE_MAX = 60


def _title_from(text: str) -> str:
    flat = " ".join(text.split())
    return flat[:_TITLE_MAX] if flat else "新话题"


def _child_kind(parent: Topic) -> TopicKind:
    # A child of the root is a topic; a child of a topic/subtopic is a subtopic.
    return TopicKind.topic if parent.kind == TopicKind.root else TopicKind.subtopic


def _opening_text(title: str, *, from_discussion: bool) -> str:
    origin = (
        "我们把这段讨论升级成一个独立话题"
        if from_discussion
        else "我们把这个待办拆成一个独立话题"
    )
    return (
        f"收到。{origin}：「{title}」。\n"
        "我先确认理解，再开始推进。下一步：明确目标和约束 → 动手 → 完成后回报结论。"
    )


class TopicService:
    def __init__(self, session: AsyncSession):
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

    async def _add_opening(self, topic: Topic, *, from_discussion: bool) -> None:
        await self._blocks.add(
            project_id=topic.project_id,
            topic_id=topic.id,
            author=CHEESE_AUTHOR,
            author_type=AuthorType.ai,
            content=_opening_text(topic.title, from_discussion=from_discussion),
            kind=BlockKind.message,
        )

    async def upgrade_block_to_topic(
        self, *, block_id: uuid.UUID, created_by: str | None = None
    ) -> Topic:
        """讨论升级 (eval A1): turn a block into its own topic; the original
        position becomes a live link, and the new topic opens with 芝士's
        opening白 (复述任务 + 下一步)."""
        block = await self._blocks.get(block_id)
        if block is None:
            raise NotFoundError("Block not found")
        # Idempotent: a second 升级 on the same block just returns the topic it
        # already created (so a double-click navigates instead of erroring).
        if block.upgraded_to_topic_id is not None:
            existing = await self._repo.get(block.upgraded_to_topic_id)
            if existing is not None:
                return existing
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
            title=_title_from(block.content),
            parent_id=parent_id,
            kind=kind,
            created_by=created_by,
            upgraded_from_block_id=block.id,
        )
        await self._blocks.set_upgraded_to_topic(block, new_topic.id)
        await self._add_opening(new_topic, from_discussion=True)
        return new_topic

    async def split_to_subtopic(
        self,
        *,
        parent_topic_id: uuid.UUID,
        title: str,
        created_by: str | None = None,
    ) -> Topic:
        """从上往下拆解 (eval A2): split a todo into a sub-topic under a topic."""
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
        await self._add_opening(new_topic, from_discussion=False)
        return new_topic

    async def get_doc(self, topic_id: uuid.UUID) -> Block | None:
        await self.get_or_404(topic_id)
        docs = await self._blocks.list_docs_for_topic(topic_id)
        return docs[0] if docs else None

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
        docs = await self._blocks.list_docs_for_topic(topic_id)
        if docs:
            doc = await self._blocks.update_content(docs[0], content)
        else:
            doc = await self._blocks.add(
                project_id=topic.project_id,
                topic_id=topic_id,
                author=author,
                author_type=AuthorType.human,
                content=content,
                kind=BlockKind.doc,
            )
        # Append-only conversation event (spec H1): the doc edit is visible.
        await self._blocks.add(
            project_id=topic.project_id,
            topic_id=topic_id,
            author=author,
            author_type=AuthorType.system,
            content=f"📝 {author} 编辑了文档",
            kind=BlockKind.event,
            refs=[str(doc.id)],
        )
        return doc

    async def return_conclusion(
        self, *, subtopic_id: uuid.UUID, conclusion: str
    ) -> Block:
        """结论回流 (spec §6.1): a sub-topic's conclusion flows back to its
        parent as a new block referencing the sub-topic."""
        sub = await self.get_or_404(subtopic_id)
        if sub.parent_id is None:
            raise ValidationError("Topic has no parent to return a conclusion to")
        return await self._blocks.add(
            project_id=sub.project_id,
            topic_id=sub.parent_id,
            author=CHEESE_AUTHOR,
            author_type=AuthorType.ai,
            content=f"【子话题结论｜{sub.title}】\n{conclusion}",
            kind=BlockKind.message,
            refs=[str(sub.id)],
        )
