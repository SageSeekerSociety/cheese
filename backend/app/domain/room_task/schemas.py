"""Task response schemas (Pydantic v2)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.presentation_meta import PresentationOut
from app.domain.room_task.models import TaskStatus

#: `PresentationOut` 住在本层（`app.domain.presentation_meta`），因为
#: `topic.schemas.TopicOut` 也要交同一个形状；在这里留一个名字，是从前读
#: `room_task.schemas.PresentationOut` 的地方不用改。
__all__ = ["PresentationOut", "TaskOut"]


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    # The room this thread hangs in — never another task: work does not nest.
    room_id: uuid.UUID
    # Its number in the project's addresses (`/projects/<slug>/tasks/<number>`).
    number: int | None = None
    title: str
    # `placeholder`: still unnamed, and each screen says so in its reader's
    # language instead of showing the stored placeholder text.
    title_source: str = "human"
    status: TaskStatus
    # 唯一的主. A room answers this with a roster; a task with one handle.
    owner_handle: str | None = None
    # 建任务时确定验收人；没有人能审阅时和历史记录可能为空。
    reviewer_handle: str | None = None
    reporter_handle: str | None = None
    contributor_handles: list[str] = Field(default_factory=list)
    created_by: str | None = None
    branch_name: str | None = None
    base_branch: str | None = None
    base_task_id: uuid.UUID | None = None
    historical_delivery_id: uuid.UUID | None = None
    pr_number: int | None = None
    pr_url: str | None = None
    delivered_head: str | None = None
    # 哪位 AI 队友在做它；None = 项目的默认队友。
    agent_handle: str | None = None
    # 这件事的实况文档。
    document_id: uuid.UUID | None = None
    # The latest accepted delivery. Accepting the last step closes the task;
    # closing a task alone does not imply delivery.
    accepted_by: str | None = None
    accepted_at: datetime | None = None
    closed_at: datetime | None = None
    upgraded_from_block_id: uuid.UUID | None = None
    # 它出自的那句话：主线消息本身，或支线里的一条回复。
    source_block_id: uuid.UUID | None = None
    # 关闭时留下的一句话。
    conclusion: str | None = None
    created_at: datetime
    updated_at: datetime
    # 看板上这一格。派生的，所以和 TopicOut 的 `running` 一样：只有明确去算它的
    # 端点会填（见 tasks 列表和 `GET /topics/{id}`），别处保持 None —— 意思是
    # 「没人算过」，不是「没有」。ORM 行上没有这个属性，from_attributes 会留默认值。
    presentation: PresentationOut | None = None
