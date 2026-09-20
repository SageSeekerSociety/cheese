"""Request shapes for 记忆整理 (see domain/memory/dream.py)."""

from datetime import datetime

from pydantic import BaseModel, Field


class DreamMergeIn(BaseModel):
    """N remembered facts that say one thing → the one line they should be."""

    replaces: list[str] = Field(default_factory=list)
    content: str = ""


class DreamProposalIn(BaseModel):
    """One pass's proposal.

    ``snapshot_at`` is what the whole thing was computed against — the newest
    ``updated_at`` 芝士 saw when it read the pools. Everything touched since is
    left alone rather than overwritten, so an omitted snapshot means "apply
    unconditionally" and is the one field worth getting right.
    """

    snapshot_at: datetime | None = None
    merges: list[DreamMergeIn] = Field(default_factory=list)
    drops: list[str] = Field(default_factory=list)
    adds: list[str] = Field(default_factory=list)
    summary: str = ""
