"""Feedback request/response schemas (Pydantic v2).

Two response shapes, on purpose:

* ``FeedbackCard`` — the list row. It carries the counters a card draws
  (support count, comment count, whether *you* supported it) because a card
  that has to fetch its own counters is N+1 requests for a page of 20.
* ``FeedbackDetail`` — everything on the row plus the child collections.

``from_row`` is the only place an ORM row becomes JSON. The counters can't come
from the row (they live in other tables), so a plain ``model_validate`` would
silently emit 0 for them; making the counters required arguments means a caller
that forgot them fails at the type checker instead.

There is deliberately no schema with a ``status`` field on the write side: status
only ever moves through ``PATCH /admin/feedback/{id}``, where the service writes
the timeline row in the same transaction. A client cannot set it directly, so a
schema that accepted it would be a lie.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.feedback.models import (
    Feedback,
    FeedbackComment,
    FeedbackKind,
    FeedbackNote,
    FeedbackPriority,
    FeedbackStatus,
    FeedbackTimeline,
    FeedbackVisibility,
)


#: 「FB-1042」。Words the frontend can show without knowing how the number is
#: stored, and the string people paste to each other.
def display_id(row: Feedback) -> str:
    return f"FB-{row.display_no}"


class FeedbackCreate(BaseModel):
    """The body of ``POST /feedback`` — also what `cheese feedback propose` sends.

    ``author_handle`` is absent by construction for people: the author is the
    verified caller. It exists here for the agent path, where one agent may file
    on someone else's behalf — the route only honours it for the submitter
    recorded in ``submitted_by_handle``, never as a free-form claim.
    """

    kind: FeedbackKind = FeedbackKind.bug
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(default="", max_length=300)
    problem: str = Field(default="", max_length=8000)
    visibility: FeedbackVisibility = FeedbackVisibility.public
    priority: FeedbackPriority = FeedbackPriority.normal
    why: str | None = Field(default=None, max_length=4000)
    expectation: str | None = Field(default=None, max_length=4000)
    what_happened: str | None = Field(default=None, max_length=4000)
    repro: str | None = Field(default=None, max_length=8000)
    evidence: str | None = Field(default=None, max_length=8000)
    logs: str | None = Field(default=None, max_length=20000)
    session_id: str | None = Field(default=None, max_length=64)
    environment: str | None = Field(default=None, max_length=255)
    topic_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    tags: list[str] = Field(default_factory=list, max_length=20)
    #: Attribution only — "an agent drafted this, a person pressed send". Never
    #: an identity: the author is always the verified caller, and the worst a
    #: forged value can do is give an agent credit it did not earn. This is why
    #: there is no matching `author_handle` field.
    submitted_by_handle: str | None = Field(default=None, max_length=64)


class FeedbackPatch(BaseModel):
    """``PATCH /admin/feedback/{id}``.

    No ``visibility`` and no ``status``:

    * ``visibility`` is the reporter's choice, made once. Letting an admin flip a
      private report public is how a report stops being safe to write, and it is
      the one edit a person cannot undo on their own.
    * ``status`` moves only through the service, which writes the timeline row
      with it. Fields named here are administrative routing — who is on it, how
      urgent it is, whether it is a security matter.

    Every field defaults to ``None`` meaning "leave it": a PATCH that omits a
    key must not clear it.
    """

    priority: FeedbackPriority | None = None
    assignee_handle: str | None = Field(default=None, max_length=64)
    security: bool | None = None


class FeedbackStatusIn(BaseModel):
    status: FeedbackStatus


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=8000)
    #: Two levels only. A reply that names a reply is folded up to its top-level
    #: ancestor by the service, so this may point at any comment and the server
    #: decides where it lands.
    parent_id: uuid.UUID | None = None


class NoteCreate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class TimelineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: FeedbackStatus
    by_handle: str | None
    at: datetime


class CommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    parent_id: uuid.UUID | None
    author_handle: str
    author_is_agent: bool
    body: str
    created_at: datetime


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    author_handle: str
    body: str
    created_at: datetime


class FeedbackCounts(BaseModel):
    all: int
    hot: int
    active: int
    resolved: int
    #: 「我的反馈」的未读数 —— 我的条目上别人留下的评论或状态变化。同一次请求返回，
    #: 因为铃铛和列表永远同时出现在管理页上。
    unread: int = 0


class FeedbackCard(BaseModel):
    """A list row. Counters are required args of ``from_row`` by design."""

    id: uuid.UUID
    display_id: str
    kind: FeedbackKind
    title: str
    summary: str
    status: FeedbackStatus
    priority: FeedbackPriority
    visibility: FeedbackVisibility
    security: bool
    author_handle: str
    author_is_agent: bool
    submitted_by_handle: str | None
    assignee_handle: str | None
    tags: list[str]
    supports: int
    comments: int
    supported: bool
    last_activity_at: datetime | None
    created_at: datetime

    @classmethod
    def from_row(
        cls,
        row: Feedback,
        *,
        supports: int,
        comments: int,
        supported: bool,
        last_activity_at: datetime | None = None,
    ) -> FeedbackCard:
        return cls(
            id=row.id,
            display_id=display_id(row),
            kind=row.kind,
            title=row.title,
            summary=row.summary,
            status=row.status,
            priority=row.priority,
            visibility=row.visibility,
            security=row.security,
            author_handle=row.author_handle,
            author_is_agent=row.author_is_agent,
            submitted_by_handle=row.submitted_by_handle,
            assignee_handle=row.assignee_handle,
            tags=list(row.tags or []),
            supports=supports,
            comments=comments,
            supported=supported,
            last_activity_at=last_activity_at,
            created_at=row.created_at,
        )


class FeedbackDetail(FeedbackCard):
    """The card plus the report body and its children."""

    problem: str
    why: str | None
    expectation: str | None
    what_happened: str | None
    repro: str | None
    evidence: str | None
    logs: str | None
    session_id: str | None
    environment: str | None
    topic_id: uuid.UUID | None
    project_id: uuid.UUID | None
    timeline: list[TimelineOut]
    thread: list[CommentOut]
    #: Admin-only; empty for everyone else. The field is present either way so
    #: the frontend has one shape, and the service is what empties it.
    notes: list[NoteOut] = Field(default_factory=list)

    @classmethod
    def from_row(
        cls,
        row: Feedback,
        *,
        supports: int,
        comments: int,
        supported: bool,
        last_activity_at: datetime | None = None,
        timeline: list[FeedbackTimeline] | None = None,
        thread: list[FeedbackComment] | None = None,
        notes: list[FeedbackNote] | None = None,
    ) -> FeedbackDetail:
        card = FeedbackCard.from_row(
            row,
            supports=supports,
            comments=comments,
            supported=supported,
            last_activity_at=last_activity_at,
        )
        return cls(
            **card.model_dump(),
            problem=row.problem,
            why=row.why,
            expectation=row.expectation,
            what_happened=row.what_happened,
            repro=row.repro,
            evidence=row.evidence,
            logs=row.logs,
            session_id=row.session_id,
            environment=row.environment,
            topic_id=row.topic_id,
            project_id=row.project_id,
            timeline=[TimelineOut.model_validate(x) for x in timeline or []],
            thread=[CommentOut.model_validate(x) for x in thread or []],
            notes=[NoteOut.model_validate(x) for x in notes or []],
        )


class FeedbackMeta(BaseModel):
    """``GET /feedback/meta`` — the vocabulary, so the client stops hardcoding it.

    The prototype shipped `STATUS_META` (colours) and `STATUS_LADDER` (order) as
    frontend constants. Colours belong to the client; *which values exist* and
    *what order they move in* belong to the server, because they are the thing a
    migration changes. Splitting them this way means adding a status value is one
    backend edit and no frontend release.
    """

    kinds: list[FeedbackKind]
    statuses: list[FeedbackStatus]
    priorities: list[FeedbackPriority]
    visibilities: list[FeedbackVisibility]
    status_ladder: list[FeedbackStatus]
    tabs: list[str]
    admin_tabs: list[str]
    hot_supports: int
    #: Whether the caller may see the admin surface. The client asks instead of
    #: guessing from a role string it can only get wrong.
    is_admin: bool


class SupportOut(BaseModel):
    """Result of POST/DELETE support — the count after the write, not a delta.

    Returning the delta would let two people supporting at once each render a
    number that never existed.
    """

    count: int
    supported: bool
