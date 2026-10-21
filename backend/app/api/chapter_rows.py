"""A conversation's chapters as its overview and its subscription read them.

`chapter_rows` builds them once: `GET /topics/{id}/chapters` returns the rows,
and the `chapters` of the room snapshot a subscription opens with
(`app.api.room_snapshot`) embeds the same ones, so the outline drawn from the
snapshot and the one the route answers with are never two different readings of
the same conversation.

It sits under `app.api` rather than beside its route
(`routes/topics_chapters.py`) because the snapshot is not a route and must not
become one that reaches a route — C4 (`routes-independent`).
"""

import uuid

from app.domain.agent_instance.adapters import project_team
from app.domain.block.adapters import dump_blocks_json
from app.domain.chapter.services import Chapters


async def chapter_rows(db, conversation_id: uuid.UUID) -> list[dict]:
    """A conversation's chapters, in reading order; also the `chapters` of the
    room snapshot a subscription opens with. The caller has authorised the
    reader."""
    # The blocks go through one batched dump, as a channel's pins do: validating
    # every row on the event loop one at a time does not let it yield.
    pairs = await Chapters(db, team=project_team).of_conversation(conversation_id)
    dumped = await dump_blocks_json([block for _, block in pairs])
    return [
        {
            "pinned_by": chapter.pinned_by,
            "pinned_at": chapter.pinned_at.isoformat(),
            "block": block,
        }
        for (chapter, _), block in zip(pairs, dumped, strict=True)
    ]
