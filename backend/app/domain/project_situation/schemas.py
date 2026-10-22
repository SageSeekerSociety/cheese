import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class SituationSource(BaseModel):
    """一块的一条出处：点开就能核对。

    ``delivery`` 是一次被采纳的交付（``card_id`` 是那张卡，在它的任务里看得到），
    ``task`` 是一个还开着的任务，``goal`` 是项目目标里的一句原话（``quote``）。
    """

    kind: Literal["delivery", "task", "goal"]
    card_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    room_id: uuid.UUID | None = None
    #: 交付的改动标题或任务标题；``goal`` 没有。
    title: str = ""
    quote: str = ""


class SituationPart(BaseModel):
    name: str
    text: str
    #: 由出处定（``card``）：引了开着的任务是 underway，只引了已采纳的交付是
    #: usable，只引了项目目标是 not_started。
    status: Literal["usable", "underway", "not_started"]
    sources: list[SituationSource]


class SituationOut(BaseModel):
    """项目总览上的「现状」（``GET /projects/{id}/situation``）。"""

    #: 上一次写下的那几块；还没写成过、或者近两周没有可写的事，是空的。
    parts: list[SituationPart]
    written_at: datetime | None
    #: 后台正在重写：写完会推一帧 ``situation``，页面再读一次。
    writing: bool
