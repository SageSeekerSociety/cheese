from datetime import datetime

from pydantic import BaseModel


class SituationOut(BaseModel):
    """项目总览上的「现状」（``GET /projects/{id}/situation``）。"""

    #: 上一次写下的 Markdown；还没写成过、或者近两周没有可写的事，是空串。
    content: str
    written_at: datetime | None
    #: 后台正在重写：写完会推一帧 ``situation``，页面再读一次。
    writing: bool
