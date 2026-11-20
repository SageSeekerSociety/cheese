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
    #: 收起来的时刻（`thread/recycle.py`）；开着的是 None。收起来的支线仍然读得到，
    #: 只是从默认那一栏挪到了「已回收」。
    recycled_at: str | None = None
