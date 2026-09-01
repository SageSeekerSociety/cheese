"""Task response schemas (Pydantic v2)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.domain.room_task.models import Residency, TaskStatus


class PresentationOut(BaseModel):
    """看板上这一格：哪一列，卡面写什么。

    两个字段一起出，因为它们是一个答案的两半 —— 列说「该谁动」，短语说「在等什
    么」，而每一列只能说属于自己的话（`room_task/presentation.py`）。客户端拿到
    的是结论，不是原料：状态从来不存库，是读的时候从事实算出来的，算的地方只有
    后端这一处，所以网页、CLI 和以后任何一个客户端不可能给出不同答案。
    """

    column: str
    display_status: str


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    # The room this thread hangs in — never another task: work does not nest.
    room_id: uuid.UUID
    title: str
    status: TaskStatus
    # Is it using one of its room's four slots right now, and since when has it
    # been waiting for one. Separate from `status` on purpose (see `Residency`),
    # and both are here because "在跑 / 排队中 / 闲着" is the first thing anyone
    # opening a room wants to know and nothing else on this object answers it.
    residency: Residency
    queued_at: datetime | None = None
    # 唯一的主. A room answers this with a roster; a task with one handle.
    owner_handle: str | None = None
    created_by: str | None = None
    agent_instance_id: uuid.UUID | None = None
    # 这条活在哪棵树上干 — many tasks share one, and that tree is the batch
    # that opens one PR. A task has no branch of its own any more.
    tree_id: uuid.UUID
    # Delivery, which is NOT the same question as `status` — work can be
    # delivered and still open, or closed with nothing delivered.
    accepted_by: str | None = None
    accepted_at: datetime | None = None
    closed_at: datetime | None = None
    upgraded_from_block_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime
    # 看板上这一格。派生的，所以和 TopicOut 的 `running` 一样：只有明确去算它的
    # 端点会填（见 tasks 列表和 `GET /topics/{id}`），别处保持 None —— 意思是
    # 「没人算过」，不是「没有」。ORM 行上没有这个属性，from_attributes 会留默认值。
    presentation: PresentationOut | None = None
