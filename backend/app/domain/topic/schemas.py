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
    # The topics ROW's mtime: it moves when the topic's own fields change
    # (title, status, session id), NOT when a block lands in it. For "was there
    # activity here", read `last_activity_at`.
    updated_at: datetime
    # 最后活动时间: the newest block in the topic, falling back to its creation.
    # Derived per query, so — like `running` — only the endpoints that ask for
    # it (list_topics/get_topic) fill it in; elsewhere it stays None.
    last_activity_at: datetime | None = None
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
    # 与我的相关性 (C2): what this topic is to the CALLER, so the sidebar can
    # show "我参与的" flat and fold everyone else's away. Two orthogonal
    # booleans rather than one relevance enum — an enum has to grow a new value
    # (and a new frontend branch) every time a way of being involved is added,
    # while these two each answer one question and compose.
    #
    # `i_participate`: I'm in the topic's roster, OR I created it, OR a card
    # here is routed to me, OR I've been @'d in it.
    # `awaits_me`: it is waiting on ME right now — a card routed to me is still
    # pending, or an @ at me is unread. This is the "永远不折叠" signal, and it
    # implies `i_participate` (every way of being awaited is also a way of
    # participating), so the folding rule only ever reads one of the two.
    #
    # Derived per caller, so — like `last_activity_at` and `running` — only the
    # endpoints that ask for them fill them in (list_topics/get_topic);
    # elsewhere both stay False, meaning "nobody computed this", not "no".
    i_participate: bool = False
    awaits_me: bool = False


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
