"""Strict inputs for journal actions; actor identity never comes from the body."""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    (``POST /topics/{id}/doc/agent``): a shortcut by its id, or what the person
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
