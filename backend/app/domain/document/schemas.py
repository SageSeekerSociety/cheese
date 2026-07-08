"""Wire schemas for the 项目文档树 (documents) REST API.

Python fields are snake_case; every model serializes to camelCase via the alias
generator (the frontend convention). Timestamps are epoch-ms ints (codebase-wide).
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from app.domain.block.models import Block
from app.domain.document.models import DocType, Document


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


def _epoch_ms(dt: datetime | None) -> int | None:
    return int(dt.timestamp() * 1000) if dt is not None else None


# ── requests ────────────────────────────────────────────────────────────────


class CreateDocumentRequest(CamelModel):
    title: str
    parent_id: int | None = None


class SaveBodyRequest(CamelModel):
    content: str


class UpdateDocumentRequest(CamelModel):
    title: str | None = None
    parent_id: int | None = None
    sort_order: float | None = None
    archived: bool | None = None


# ── responses ───────────────────────────────────────────────────────────────


class DocumentSummary(CamelModel):
    id: int
    project_id: int
    parent_id: int | None = None
    title: str
    doc_type: str
    sort_order: float
    archived: bool
    created_by: int
    created_at: int
    updated_at: int


class DocumentTreeNode(DocumentSummary):
    children: list["DocumentTreeNode"] = []


class DocumentNode(CamelModel):
    """One DOC_NODE block — a stable anchor for comments / cross-view highlights."""

    id: int
    node_type: str | None = None
    struct_order: float | None = None
    content: str
    edited_at: int | None = None


class DocumentDetail(CamelModel):
    document: DocumentSummary
    content: str  # 整篇 markdown(DOC_ROOT 正文)
    nodes: list[DocumentNode] = []


DocumentTreeNode.model_rebuild()


# ── converters ──────────────────────────────────────────────────────────────


def to_summary(doc: Document) -> DocumentSummary:
    created_at = _epoch_ms(doc.created_at) or 0
    updated_at = _epoch_ms(doc.updated_at) or 0
    return DocumentSummary(
        id=doc.id,
        project_id=doc.project_id,
        parent_id=doc.parent_id,
        title=doc.title,
        doc_type=DocType(doc.doc_type).name.lower(),
        sort_order=doc.sort_order,
        archived=doc.archived,
        created_by=doc.created_by,
        created_at=created_at,
        updated_at=updated_at,
    )


def to_node(block: Block) -> DocumentNode:
    return DocumentNode(
        id=block.id,
        node_type=block.node_type,
        struct_order=block.struct_order,
        content=block.content,
        edited_at=_epoch_ms(block.edited_at),
    )
