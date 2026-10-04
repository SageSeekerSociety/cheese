"""Strict inputs for journal actions; actor identity never comes from the body."""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.living_doc.models import Document, DocumentNode
from app.domain.living_doc.services import content_hash


class RestoreIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    expected_version: int = Field(ge=0)
    operation_id: uuid.UUID


class DocumentIn(BaseModel):
    """A new document of the project's own (``POST /projects/{id}/documents``)."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="", max_length=200)
    #: What it says to begin with, in Markdown; checked like any write from
    #: outside an editor.
    content: str | None = Field(default=None, max_length=500_000)
    #: Copy what this document says now (另存为文档): a room's document kept
    #: beyond its room. The copy and the original change apart from then on.
    copy_of: uuid.UUID | None = None

    @model_validator(mode="after")
    def _one_source(self) -> "DocumentIn":
        if self.content is not None and self.copy_of is not None:
            raise ValueError("give content or copy_of, not both")
        return self


class DocumentRenameIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(max_length=200)


class PassageEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Text of the document's Markdown, occurring in it exactly once.
    old: str = Field(min_length=1)
    new: str


class PassageEditsIn(BaseModel):
    """Change passages of the living document (``POST /documents/{id}/edits``)."""

    model_config = ConfigDict(extra="forbid")

    edits: list[PassageEdit] = Field(min_length=1, max_length=50)
    #: "suggest" proposes the changes instead of making them. Left out, the
    #: platform decides (see ``app.api.doc_edits``).
    mode: Literal["direct", "suggest"] | None = None
    #: Why, in a sentence: shown next to a suggestion.
    reason: str | None = Field(default=None, max_length=500)


class SelectionIn(BaseModel):
    """A selection in the living document, as the editor writes it out."""

    model_config = ConfigDict(extra="forbid")

    #: The Markdown of the top-level blocks holding the selection (a run of
    #: them when it spans paragraphs), as the editor serializes it.
    block: str = Field(min_length=1, max_length=20000)
    #: The selection, as offsets into ``block``.
    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode="after")
    def _inside(self) -> "SelectionIn":
        if not self.start < self.end <= len(self.block):
            raise ValueError("the selection is not inside its block")
        return self


class AgentAskIn(BaseModel):
    """Ask the room's AI teammate from the document
    (``POST /documents/{id}/agent``): a shortcut by its id, or what the person
    wrote, about a selection or the whole document."""

    model_config = ConfigDict(extra="forbid")

    #: The box's conversation, to go on with; none starts one.
    conversation: uuid.UUID | None = None
    preset: str | None = Field(default=None, max_length=32)
    text: str = Field(default="", max_length=4000)
    selection: SelectionIn | None = None

    @model_validator(mode="after")
    def _asked(self) -> "AgentAskIn":
        self.text = self.text.strip()
        if not self.preset and not self.text:
            raise ValueError("nothing was asked")
        return self


def document_snapshot(doc: Document) -> dict:
    """What a reader of a document gets: its text and which version that is.
    ``topic_id`` is the room it belongs to, if any."""
    return {
        "id": str(doc.id),
        "project_id": str(doc.project_id),
        "topic_id": str(doc.room_id) if doc.room_id else None,
        "kind": doc.kind,
        "title": doc.title,
        "content": doc.content,
        "doc_version": doc.version,
        "content_hash": content_hash(doc.content),
        "author": doc.author,
        "created_at": doc.created_at.isoformat(),
        "updated_at": doc.updated_at.isoformat(),
    }


def document_row(doc: Document) -> dict:
    """A document as a list names it: everything but what it says."""
    return {
        "id": str(doc.id),
        "project_id": str(doc.project_id),
        "topic_id": str(doc.room_id) if doc.room_id else None,
        "kind": doc.kind,
        "title": doc.title,
        "doc_version": doc.version,
        "author": doc.author,
        "created_at": doc.created_at.isoformat(),
        "updated_at": doc.updated_at.isoformat(),
    }


def node_out(node: DocumentNode, doc: Document) -> dict:
    """One top-level block of a document, in the shape the client reads a
    block in (``kind`` "doc_node", its place in ``struct_order``)."""
    return {
        "id": str(node.id),
        "kind": "doc_node",
        "project_id": str(doc.project_id),
        "topic_id": str(doc.room_id) if doc.room_id else None,
        "struct_parent": str(doc.id),
        "struct_order": node.position,
        "node_type": node.node_type,
        "content": node.content,
        "author": node.author,
        "created_at": node.created_at.isoformat(),
    }
