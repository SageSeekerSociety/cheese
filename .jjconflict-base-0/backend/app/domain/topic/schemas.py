"""Topic request/response schemas (Pydantic v2)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.topic.models import TopicKind, TopicStatus


class TopicCreate(BaseModel):
    project_id: uuid.UUID
    title: str = Field(min_length=1, max_length=300)
    parent_id: uuid.UUID | None = None
    created_by: str | None = None


class TopicOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    parent_id: uuid.UUID | None
    title: str
    kind: TopicKind
    status: TopicStatus
    created_at: datetime
    updated_at: datetime
    # Lifecycle markers (spec §6.3): who accepted, when archived, and — for an
    # upgraded topic — which block it grew from (for the 活引用 back-link).
    accepted_by: str | None = None
    accepted_at: datetime | None = None
    archived_at: datetime | None = None
    upgraded_from_block_id: uuid.UUID | None = None
    # 本轮是否在跑 (TurnRunner, in-memory — separate from `status`/归档: a topic
    # can be "active" and idle, or "active" and mid-turn). False unless the
    # caller explicitly fills it in (see list_topics/get_topic) — the ORM model
    # has no such attribute, so from_attributes just leaves the default.
    running: bool = False


class UpgradeBlockIn(BaseModel):
    created_by: str | None = None


class SplitIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    created_by: str | None = None
    # 任务简报: what the 分身 is expected to do, in the splitter's own words.
    # Preset as the child's living doc so the kickoff turn starts informed.
    brief: str | None = None


class ConclusionIn(BaseModel):
    conclusion: str = Field(min_length=1)


class DocEditIn(BaseModel):
    content: str
    author: str = "anonymous"


class BackgroundTaskIn(BaseModel):
    """`cheese await` registering a command it is about to run in its sandbox."""

    command: str = Field(min_length=1, max_length=4000)
    label: str = Field(default="", max_length=120)
    # Wall-clock ceiling the sandbox-side child enforces; the wake token is
    # minted to outlive it. Bounds are re-checked in awaited_tasks.register.
    timeout_s: int = 3600
    # Where the child is writing the command's full output, so the wake can point
    # at it (the tail alone is bounded).
    log_path: str = Field(default="", max_length=500)


class BackgroundTaskDoneIn(BaseModel):
    """The detached child reporting how the command ended."""

    exit_code: int
    tail: str = ""
    duration_s: float = 0.0
