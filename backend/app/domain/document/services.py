"""业务逻辑:项目文档树 (DocumentService)。

Route → **Service** → Repository → Model。所有读写操作都注入 actor_id 并强制校验
项目成员身份(本阶段权限扁平:任何项目成员——人或 agent,都是 user——可读可写整棵
文档树,非成员 403)。文档正文是活文档,落在 `block` substrate 上:保存正文时用
`doc_tree.reconcile_nodes` 计算块级 diff,未变的节点保留其 id(评论/引用锚点得以存活)。
改文档即指令,故每次保存后追加一个 EVENT 块。
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError
from app.domain.block.doc_tree import markdown_to_nodes, reconcile_nodes
from app.domain.block.models import Block
from app.domain.block.repositories import BlockRepository
from app.domain.document.models import Document
from app.domain.document.repositories import DocumentRepository
from app.domain.document.schemas import (
    DocumentDetail,
    DocumentSummary,
    DocumentTreeNode,
    to_node,
    to_summary,
)
from app.domain.project.repositories import ProjectMembershipRepository

_EDIT_EVENT_TEXT = "📝 编辑了文档"

_EXPORT_DEFAULT_TITLE = "聊天记录导出"


@dataclass
class ExportMessage:
    """One chat message flattened for export — purely structural (作者/时间/正文),
    no NL/语义推断 (万物皆块: chat block → document block, structure only)."""

    author_name: str
    text: str
    timestamp: datetime | None = None


def render_messages_markdown(messages: list[ExportMessage]) -> str:
    """Render an ordered (chronological) list of chat messages into a markdown
    document body: each message becomes a section — a bold author line (with an
    optional timestamp) followed by the message text. Structure only."""
    sections: list[str] = []
    for msg in messages:
        if msg.timestamp is not None:
            header = f"**{msg.author_name}** · {msg.timestamp.isoformat()}"
        else:
            header = f"**{msg.author_name}**"
        sections.append(f"{header}\n\n{msg.text}")
    return "\n\n".join(sections)


class DocumentService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._docs = DocumentRepository(session)
        self._blocks = BlockRepository(session)
        self._members = ProjectMembershipRepository(session)

    # -- authz -----------------------------------------------------------------

    async def _require_member(self, project_id: int, actor_id: int) -> None:
        rel = await self._members.get_relation(project_id, actor_id)
        if rel is None:
            raise ForbiddenError("Not a member of this project")

    async def _get_doc(self, document_id: int) -> Document:
        doc = await self._docs.get(document_id)
        if doc is None:
            raise NotFoundError.for_resource("document", document_id)
        return doc

    # -- reads -----------------------------------------------------------------

    async def list_tree(self, project_id: int, actor_id: int) -> list[DocumentTreeNode]:
        await self._require_member(project_id, actor_id)
        docs = await self._docs.list_for_project(project_id)
        nodes = {d.id: DocumentTreeNode(**to_summary(d).model_dump(), children=[]) for d in docs}
        roots: list[DocumentTreeNode] = []
        for d in docs:
            node = nodes[d.id]
            parent = nodes.get(d.parent_id) if d.parent_id is not None else None
            if parent is not None:
                parent.children.append(node)
            else:
                roots.append(node)
        return roots

    async def get_document(self, document_id: int, actor_id: int) -> DocumentDetail:
        doc = await self._get_doc(document_id)
        await self._require_member(doc.project_id, actor_id)
        content = ""
        nodes: list[Block] = []
        if doc.doc_root_block_id is not None:
            root = await self._blocks.get(doc.doc_root_block_id)
            if root is not None:
                content = root.content
                nodes = await self._blocks.list_doc_nodes(root.id)
        return DocumentDetail(
            document=to_summary(doc),
            content=content,
            nodes=[to_node(b) for b in nodes],
        )

    # -- writes ----------------------------------------------------------------

    async def create_document(
        self, project_id: int, actor_id: int, title: str, parent_id: int | None = None
    ) -> DocumentSummary:
        await self._require_member(project_id, actor_id)
        root = await self._blocks.add_doc_root(
            project_id=project_id, author_id=actor_id, content=""
        )
        sort_order = await self._docs.next_sort_order(project_id, parent_id)
        doc = await self._docs.create(
            project_id=project_id,
            parent_id=parent_id,
            title=title,
            created_by=actor_id,
            doc_root_block_id=root.id,
            sort_order=sort_order,
        )
        return to_summary(doc)

    async def save_body(self, document_id: int, actor_id: int, content: str) -> DocumentDetail:
        doc = await self._get_doc(document_id)
        await self._require_member(doc.project_id, actor_id)
        if doc.doc_root_block_id is None:
            root = await self._blocks.add_doc_root(
                project_id=doc.project_id, author_id=actor_id, content=""
            )
            doc.doc_root_block_id = root.id
        else:
            fetched = await self._blocks.get(doc.doc_root_block_id)
            if fetched is None:
                raise NotFoundError.for_resource("doc_root", doc.doc_root_block_id)
            root = fetched

        # 重新保存完全相同的 markdown 是 no-op:不改块、不追加事件。
        if root.content == content:
            return await self.get_document(document_id, actor_id)

        existing = await self._blocks.list_doc_nodes(root.id)
        by_id = {b.id: b for b in existing}
        plan = reconcile_nodes(
            [(b.id, b.content) for b in existing], markdown_to_nodes(content)
        )

        for bid in plan.delete_ids:
            await self._blocks.delete(by_id[bid])
        for op in plan.reuse:
            block = by_id[op.block_id]
            desired_order = float(op.idx)
            if block.node_type != op.node.node_type or block.struct_order != desired_order:
                await self._blocks.update_node(
                    block, node_type=op.node.node_type, struct_order=desired_order
                )
        for idx, node in plan.insert:
            await self._blocks.add_doc_node(
                project_id=doc.project_id,
                author_id=actor_id,
                struct_parent_id=root.id,
                content=node.content,
                node_type=node.node_type,
                struct_order=float(idx),
            )

        await self._blocks.update_content(root, content)
        await self._docs.touch(doc)
        # 改文档即指令:追加一个 EVENT 块,refs 指向 DOC_ROOT。
        await self._blocks.add_event(
            project_id=doc.project_id,
            author_id=actor_id,
            content=_EDIT_EVENT_TEXT,
            refs=[root.id],
        )
        return await self.get_document(document_id, actor_id)

    async def export_messages(
        self,
        project_id: int,
        actor_id: int,
        messages: list[ExportMessage],
        title: str | None = None,
        document_id: int | None = None,
    ) -> DocumentSummary:
        """Bridge chat blocks → a document (导出到文档). Renders ``messages`` to markdown,
        then either appends to an existing ``document_id`` (fetch current body + append)
        or creates a NEW project document and saves the body. Project-membership 校验由
        create_document / save_body / get_document 内部强制。Returns the document summary."""
        body = render_messages_markdown(messages)
        if document_id is not None:
            detail = await self.get_document(document_id, actor_id)
            combined = f"{detail.content}\n\n{body}" if detail.content.strip() else body
            result = await self.save_body(document_id, actor_id, combined)
            return result.document
        summary = await self.create_document(project_id, actor_id, title or _EXPORT_DEFAULT_TITLE)
        await self.save_body(summary.id, actor_id, body)
        return summary

    async def rename(self, document_id: int, actor_id: int, title: str) -> DocumentSummary:
        doc = await self._get_doc(document_id)
        await self._require_member(doc.project_id, actor_id)
        await self._docs.update(doc, title=title)
        return to_summary(doc)

    async def move(
        self, document_id: int, actor_id: int, parent_id: int | None, sort_order: float | None
    ) -> DocumentSummary:
        doc = await self._get_doc(document_id)
        await self._require_member(doc.project_id, actor_id)
        await self._docs.update(
            doc, parent_id=parent_id, set_parent=True, sort_order=sort_order
        )
        return to_summary(doc)

    async def archive(self, document_id: int, actor_id: int, archived: bool) -> DocumentSummary:
        doc = await self._get_doc(document_id)
        await self._require_member(doc.project_id, actor_id)
        await self._docs.update(doc, archived=archived)
        return to_summary(doc)

    async def update_document(
        self, document_id: int, actor_id: int, fields: dict[str, object]
    ) -> DocumentSummary:
        """PATCH 入口:只应用调用方显式提供的字段(rename / move / archive 的组合)。"""
        doc = await self._get_doc(document_id)
        await self._require_member(doc.project_id, actor_id)
        title = fields.get("title") if "title" in fields else None
        sort_order = fields.get("sort_order") if "sort_order" in fields else None
        archived = fields.get("archived") if "archived" in fields else None
        set_parent = "parent_id" in fields
        parent_id = fields.get("parent_id") if set_parent else None
        await self._docs.update(
            doc,
            title=title if isinstance(title, str) else None,
            parent_id=parent_id if isinstance(parent_id, int) else None,
            set_parent=set_parent,
            sort_order=float(sort_order) if isinstance(sort_order, int | float) else None,
            archived=archived if isinstance(archived, bool) else None,
        )
        return to_summary(doc)

    async def delete_document(self, document_id: int, actor_id: int) -> None:
        """删除文档 + 其 DOC_ROOT / DOC_NODE 块。

        子文档处理:重挂到被删文档的父节点(reparent-to-parent),而非级联删除或
        禁止删除,以免误伤子文档内容。
        """
        doc = await self._get_doc(document_id)
        await self._require_member(doc.project_id, actor_id)

        children = await self._docs.children_of(doc.project_id, doc.id)
        for child in children:
            await self._docs.update(child, parent_id=doc.parent_id, set_parent=True)

        if doc.doc_root_block_id is not None:
            root = await self._blocks.get(doc.doc_root_block_id)
            if root is not None:
                for node in await self._blocks.list_doc_nodes(root.id):
                    await self._blocks.delete(node)
                await self._blocks.delete(root)
        await self._docs.delete(doc)
