"""Topic request/response schemas (Pydantic v2)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.room_task.schemas import PresentationOut
from app.domain.topic.models import TopicKind, TopicStatus


class TopicCreate(BaseModel):
    project_id: uuid.UUID
    title: str = Field(min_length=1, max_length=300)
    parent_id: uuid.UUID | None = None
    created_by: str | None = None
    # Which agent works here. Omitted = the project's default, and it keeps
    # following that default rather than freezing a copy of it now.
    agent_instance_id: uuid.UUID | None = None


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
    # NULL = this topic uses the project's default agent.
    agent_instance_id: uuid.UUID | None = None
    # 本轮是否在跑 (AgentWorkRunner, in-memory — separate from `status`/归档: a topic
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
    # 看板上这一格 —— 同一个 `presentation` 结构，一条活和一个房间用同一套词，因为
    # 侧栏把它们画在一起。和上面几个派生字段同一条规矩：只有 list_topics/get_topic
    # 会填，别处是 None（「没人算过」）。
    presentation: PresentationOut | None = None


class UpgradeBlockIn(BaseModel):
    created_by: str | None = None


class SplitIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    created_by: str | None = None
    # 任务简报: what the 分身 is expected to do, in the splitter's own words.
    # Preset as the child's living doc so the kickoff turn starts informed.
    brief: str | None = None
    # 这条活要碰哪些路径. Separate from `brief` on purpose: the brief is written
    # once and can never be changed, and a claim always grows as work reaches
    # files nobody predicted. A path ending in `/` is a directory.
    paths: list[str] = Field(default_factory=list)


class ClaimIn(BaseModel):
    """Add to what this piece of work says it will touch (`cheese claim`)."""

    paths: list[str] = Field(default_factory=list)


class CheckResultIn(BaseModel):
    """What the quick check said (`cheese check`).

    `ok=False` covers a red check AND one that ran out of its time budget: the
    budget is the point, so exceeding it is a result, not an absence of one.
    """

    ok: bool
    detail: str = ""


class LockIn(BaseModel):
    """Take or give back one of the room's two locks.

    `file` names one path and guards a whole-file overwrite; `heavy` is the
    room's single lane for test runs, dependency installs and dev servers, and
    names nothing.
    """

    kind: str = Field(pattern="^(file|heavy)$")
    resource: str = ""


class ConclusionIn(BaseModel):
    """收卡. Empty means "the worker's last word stands" — the platform already
    wrote it on the card, so the room usually has nothing to add."""

    conclusion: str = ""


class BindSubagentIn(BaseModel):
    """认领: which worker in this room's session is doing this piece of work."""

    agent_id: str = Field(min_length=1, max_length=64)


class RelayIn(BaseModel):
    """母子传话 (`cheese tell`): one message across the parent/child edge.

    ``target`` is a topic id, a ``<#id>`` reference token, or a title — resolved
    against the sender's parent + direct children only (app.domain.topic.relay).
    """

    target: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)


class DocEditIn(BaseModel):
    content: str
    author: str = "anonymous"
    # The `doc_version` this edit is based on — 0 for "there is no doc yet".
    # Required, and deliberately so: this doc is only ever written whole, so a
    # writer with no version is a writer about to erase whatever it did not
    # read. Everything that writes here has just read the doc.
    expected_version: int
