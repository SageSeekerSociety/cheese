"""Strict inputs for journal actions; actor identity never comes from the body."""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RestoreIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    expected_version: int = Field(ge=0)
    operation_id: uuid.UUID


class PassageEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Text of the document's Markdown, occurring in it exactly once.
    old: str = Field(min_length=1)
    new: str


class PassageEditsIn(BaseModel):
    """Change passages of the living document (``POST /topics/{id}/doc/edits``)."""

    model_config = ConfigDict(extra="forbid")

    edits: list[PassageEdit] = Field(min_length=1, max_length=50)
    #: "suggest" proposes the changes instead of making them. Left out, the
    #: platform decides (see ``app.api.doc_edits``).
    mode: Literal["direct", "suggest"] | None = None
    #: Why, in a sentence: shown next to a suggestion.
    reason: str | None = Field(default=None, max_length=500)


class RewriteIn(BaseModel):
    """Rewrite a selection (``POST /topics/{id}/doc/rewrite``)."""

    model_config = ConfigDict(extra="forbid")

    #: The Markdown of the top-level blocks holding the selection (a run of
    #: them when it spans paragraphs), as the editor serializes it.
    block: str = Field(min_length=1, max_length=20000)
    #: The selection, as offsets into ``block``.
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    instruction: str = Field(min_length=1, max_length=2000)
