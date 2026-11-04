"""Block response schemas (Pydantic v2)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

# `AuthorType` and `BlockKind` are re-exported: a route that writes a block
# reads them from here, not from `block.models` / `event_author`, so it does
# not depend on the table definitions directly.
from app.domain.block.models import (
    Block,
)
from app.domain.block.models import (
    BlockKind as BlockKind,
)
from app.domain.event_author import (
    AuthorType as AuthorType,
)
from app.domain.notice_meta import shown_as


class ReactionOut(BaseModel):
    """One aggregated emoji reaction group on a block (Slack-style chip)."""

    emoji: str
    count: int
    authors: list[str]


class ReactionToggleIn(BaseModel):
    """POST /blocks/{id}/reactions — Slack semantics: toggles (emoji, caller)."""

    emoji: str = Field(min_length=1, max_length=32)


class BlockOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    # Its place in the order the conversation's blocks were stored (`Block.seq`):
    # a page that missed some asks for those after the largest it holds.
    seq: int
    kind: BlockKind
    author_type: AuthorType
    author: str
    content: str
    # reply_to = 对话树, refs[] = 引用(决策/PR/现场).
    reply_to: uuid.UUID | None
    # Render-by-type: mimeType of an artifact block (set on artifact blocks).
    mime_type: str | None = None
    refs: list[str] = []
    # The agent turn that produced this block (R4): groups a turn's blocks.
    turn_id: uuid.UUID | None = None
    # Structured event payload (kind=event): {"tool", "arg", "platform"} — the
    # UI translates/classifies from this; `content` is the baked-text fallback.
    meta: dict | None = None
    # Aggregated emoji reactions (Slack chips). Not a model attribute — list
    # endpoints fill it from one batch query (see reactions_for_blocks).
    reactions: list[ReactionOut] = []
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def shown_as(self) -> str | None:
        """How a platform line is drawn in the conversation (`notice_meta.shown_as`);
        None for anything that is not a platform line. Worked out on the way out,
        from the same table that decides at write time whether it is shown, so a
        line written before the table reads the same as one written after it."""
        if self.kind != BlockKind.event:
            return None
        return shown_as(self.meta, in_turn=self.turn_id is not None)


#: How much of a quoted message travels with the reply when its parent is not in
#: the page. The quote bar itself shows one short line (`replySnippet`); this cap
#: keeps a page of replies from carrying whole reports, while leaving enough for
#: the reader to recognise the line.
REPLY_PREVIEW_CHARS = 200


def reply_preview(parent: Block) -> dict:
    """The one-line stand-in for a message quoted by one on this page.

    The reader's timeline may not hold the parent — it can be on an older page —
    so without this the reply cue would silently disappear. Only what the cue
    draws travels: who said it, what kind it is, and the first
    ``REPLY_PREVIEW_CHARS`` characters. The parent itself is not added to the
    page or kept by the reader.
    """
    return {
        "id": str(parent.id),
        # `str()` on the two StrEnums gives the same text as `.value`; reading
        # `author_type.value` here would trip the guard that keeps this column
        # from being compared as a string (tests/unit/test_author_type_two_values).
        "kind": str(parent.kind),
        "author_type": str(parent.author_type),
        "author": parent.author,
        "content": parent.content[:REPLY_PREVIEW_CHARS],
        "mime_type": parent.mime_type,
    }
