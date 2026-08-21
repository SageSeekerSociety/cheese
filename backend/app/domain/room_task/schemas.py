"""Task response schemas (Pydantic v2)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.domain.room_task.models import TaskStatus


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    # The room this thread hangs in — never another task: work does not nest.
    room_id: uuid.UUID
    title: str
    status: TaskStatus
    # 唯一的主. A room answers this with a roster; a task with one handle.
    owner_handle: str | None = None
    created_by: str | None = None
    agent_instance_id: uuid.UUID | None = None
    branch_name: str | None = None
    # Delivery, which is NOT the same question as `status` — work can be
    # delivered and still open, or closed with nothing delivered.
    accepted_by: str | None = None
    accepted_at: datetime | None = None
    closed_at: datetime | None = None
    upgraded_from_block_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime
