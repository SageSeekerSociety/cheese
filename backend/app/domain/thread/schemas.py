"""支线 request/response schemas (Pydantic v2)."""

from pydantic import BaseModel


class ThreadOut(BaseModel):
    """A 支线 as the routes hand it back: enough for a list row, nothing more.

    Ids are strings on the wire (they are ``uuid`` columns underneath), and
    ``last_reply_at`` is null until someone replies.
    """

    id: str
    room_id: str
    root_block_id: str
    reply_count: int
    last_reply_at: str | None
    created_by: str
    created_at: str
