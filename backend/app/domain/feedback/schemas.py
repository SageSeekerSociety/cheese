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

Author avatars are the same kind of fact and take the same route. They live on
``UserProfile``, not on the feedback row, so ``from_row`` demands a page-wide
``avatars`` map (handle → avatar id) and a caller that forgot it fails at the
type checker the same way. The map holds **only the people who actually picked
an avatar**: registration hardcodes the global default, so "has an avatar_id" is
not the question, and filling the gap with the default would draw every
never-chooser as the one shared face. Absent means the UI draws its coloured
initial — see `UserProfileRepository.chosen_avatar_ids`.

There is deliberately no schema with a ``status`` field on the write side: status
only ever moves through ``PATCH /admin/feedback/{id}``, where the service writes
the timeline row in the same transaction. A client cannot set it directly, so a
schema that accepted it would be a lie.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
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
    """The body of ``POST /feedback``, and of accepting a proposal card.

    There is no ``author_handle`` field, on purpose: the author is the verified
    caller, never a string the client chose. On the accept path the author comes
    from the card instead (the agent that found it) and the caller is recorded
    as the submitter — the route reads both, so the body still carries neither.

    ``topic_id`` / ``project_id`` are not here either, and for the same reason:
    自从「提出它的那个房间」成为可见性并集的一档（结论 47），``topic_id`` 就是那一
    档的**授权键**——客户端说了算的键就是客户端填得错的键。给一条反馈安上房间的路
    只有一条：发送提案卡，而它从自己的 URL 解出房间（``AcceptedProposal``）。于是
    「这条反馈有房间来源」和「它是那个房间里的一张卡发出来的」是同一件事，而后者正
    是「那个房间的人本来就看过它的内容」成立的那个条件。
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
    tags: list[str] = Field(default_factory=list, max_length=20)


class FeedbackProposalIn(BaseModel):
    """The body of ``POST /topics/{topic_id}/feedback-proposals`` — and therefore
    what `cheese feedback propose` sends.

    The shape is cc's `SendFeedback` draft (§5.0/§5.5), which the requirement
    asked us to copy: a fixed skeleton, bullets rather than prose, and —
    the part that matters most — **「谁说的」必须自证**. `user_said` is a required
    position that has to hold either a quote or the sentence 「用户没有就这个
    问题说过话」. A required slot with one honest way to fill it beats a prompt
    begging the model to be truthful.
    """

    kind: FeedbackKind = FeedbackKind.bug
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(default="", max_length=300)
    problem: str = Field(default="", max_length=8000)
    visibility: FeedbackVisibility = FeedbackVisibility.public
    #: 判断依据 —— 「这不是用户的使用方式问题」的依据。
    why: str | None = Field(default=None, max_length=4000)
    expectation: str | None = Field(default=None, max_length=4000)
    what_happened: str | None = Field(default=None, max_length=4000)
    repro: str | None = Field(default=None, max_length=8000)
    evidence: str | None = Field(default=None, max_length=8000)
    logs: str | None = Field(default=None, max_length=20000)
    session_id: str | None = Field(default=None, max_length=64)
    environment: str | None = Field(default=None, max_length=255)
    tags: list[str] = Field(default_factory=list, max_length=20)
    #: 用户原话，或者那句规定好的「用户没有就这个问题说过话」。必填，理由见类说明。
    user_said: str = Field(min_length=1, max_length=2000)


class FeedbackProposalOut(BaseModel):
    """A proposal card as the chat column reads it."""

    block_id: uuid.UUID
    author_handle: str
    authored_at: datetime
    payload: dict


class FeedbackProposalResult(BaseModel):
    block_id: uuid.UUID
    fingerprint: str


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
    """One rung of the ladder: who moved the status, to what, when.

    No avatar on purpose. Nothing draws a face on the ladder — it reads as a
    line of 「谁在什么时候改成了什么」 — so carrying one would buy a second
    handle→avatar pass over the detail page for a picture no view renders.
    """

    model_config = ConfigDict(from_attributes=True)

    status: FeedbackStatus
    by_handle: str | None
    at: datetime


class CommentOut(BaseModel):
    id: uuid.UUID
    parent_id: uuid.UUID | None
    author_handle: str
    author_is_agent: bool
    #: 作者挑过的头像 id，没挑过就是 None（客户端画首字母）。和卡片的
    #: `author_avatar_id` 是同一份契约，理由也一样。
    author_avatar_id: int | None
    body: str
    #: 这条在回谁。顶层评论恒为 None；历史回复也是 None（那一列是后加的，
    #: 折掉的目标没有任何地方记过，补不出来）。None 的含义是「不知道」，
    #: 客户端不显示这一句就是了 —— 不要拿它当「没回谁」去编一个。
    reply_to_handle: str | None
    #: 点赞数。和卡片的 `supports` 是同一类事实，走同一处整页批量取 ——
    #: 一条回复一次查询就是 N+1，而帖子正是一页回复。
    likes: int
    #: 调用者点过没有。
    liked: bool
    #: 调用者能不能删这一条。**服务端算**：判据（作者本人或管理员）和
    #: `DELETE /feedback/{id}/comments/{comment_id}` 共用 `may_delete_comment`
    #: 一处，所以按钮只要照这个布尔值画就不会出现「画得出来、点下去 403」。
    #: 客户端自己拼一遍 `handle == mine || isAdmin` 就是这个仓库已经吃过一次
    #: 的亏（`deployed` 那次：按钮亮着、服务端回 412）。
    can_delete: bool
    #: **服务端数得出来的**回复总数（顶层评论才有意义；回复恒为 0）。
    #: 评论是分页取的，所以「手上这几条回复」和「这栋楼一共有几条回复」是两件事 ——
    #: 少了这个数，客户端只能拿已经取回来的条数当全部，「展开更多」就永远不知道该
    #: 去取下一页、还是只把已经拿到的摊开。
    reply_count: int = 0
    #: 这一栋楼**楼内**的下一页游标，`None` 表示楼里的回复已经带全了。
    #:
    #: 只有顶层评论有值（回复恒为 `None`）—— 「这栋楼还有没有下一段」挂在回复上没有
    #: 任何一条读路径会去看它。字符串不透明：客户端原样带回
    #: `GET /feedback/{id}/comments?parent_id=…&after=…`，不解析、不自己拼。
    replies_next_cursor: str | None = None
    created_at: datetime

    @classmethod
    def from_row(
        cls,
        row: FeedbackComment,
        *,
        avatars: Mapping[str, int],
        likes: int,
        liked: bool,
        can_delete: bool,
        reply_count: int = 0,
        replies_next_cursor: str | None = None,
    ) -> CommentOut:
        return cls(
            id=row.id,
            parent_id=row.parent_id,
            author_handle=row.author_handle,
            author_is_agent=row.author_is_agent,
            author_avatar_id=avatars.get(row.author_handle),
            body=row.body,
            reply_to_handle=row.reply_to_handle,
            likes=likes,
            liked=liked,
            can_delete=can_delete,
            reply_count=reply_count,
            replies_next_cursor=replies_next_cursor,
            created_at=row.created_at,
        )


class NoteOut(BaseModel):
    id: uuid.UUID
    author_handle: str
    #: 同 `CommentOut.author_avatar_id`。
    author_avatar_id: int | None
    body: str
    created_at: datetime

    @classmethod
    def from_row(cls, row: FeedbackNote, *, avatars: Mapping[str, int]) -> NoteOut:
        return cls(
            id=row.id,
            author_handle=row.author_handle,
            author_avatar_id=avatars.get(row.author_handle),
            body=row.body,
            created_at=row.created_at,
        )


class FeedbackCounts(BaseModel):
    all: int
    hot: int
    active: int
    resolved: int
    #: 「上线」单独一个数。`resolved` 装的是**修复 + 上线**这一对（`_tab_where` 的
    #: docstring 写着那条决定），这里只是**另外**多给一个，栏位口径不变 —— 看板上
    #: 「解决」和「上线」要画成两条线，缺了它「上线了多少」在这个平台上没被数过。
    deployed: int = 0
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
    #: 作者挑过的头像 id，没挑过就是 None —— 客户端画首字母，不拿默认头像顶替。
    #: 判据见模块顶部和 `UserProfileRepository.chosen_avatar_ids`。
    author_avatar_id: int | None
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
        avatars: Mapping[str, int],
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
            author_avatar_id=avatars.get(row.author_handle),
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
    #: 顶层评论还有下一页时，这里是下一页的游标（不透明字符串，原样带回来即可）；
    #: `None` 表示这条反馈的评论已经全在这一页里了。「还有没有」由服务端回答，
    #: 客户端按条数猜（比如「取满一页就还有」）在最后一页正好是整页时会多要一次空页。
    thread_next_cursor: str | None = None
    #: Admin-only; empty for everyone else. The field is present either way so
    #: the frontend has one shape, and the service is what empties it.
    notes: list[NoteOut] = Field(default_factory=list)
    #: 调用者能不能删掉**整条反馈**。**服务端算**，和 `DELETE /feedback/{id}` 共用
    #: `may_delete_feedback` 一处判据 —— 作者（写它的那个 handle，或按下发送的那个）
    #: 与平台管理员各一档。客户端自己拼一遍 `handle == mine || isAdmin` 就是「按钮
    #: 画得出来、点下去 403」的来源，评论那一层已经为此付过学费。
    can_delete: bool = False

    @classmethod
    def from_row(
        cls,
        row: Feedback,
        *,
        supports: int,
        comments: int,
        supported: bool,
        avatars: Mapping[str, int],
        last_activity_at: datetime | None = None,
        timeline: list[FeedbackTimeline] | None = None,
        thread: list[CommentOut] | None = None,
        thread_next_cursor: str | None = None,
        notes: list[FeedbackNote] | None = None,
        can_delete: bool = False,
    ) -> FeedbackDetail:
        card = FeedbackCard.from_row(
            row,
            supports=supports,
            comments=comments,
            supported=supported,
            avatars=avatars,
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
            # Comments arrive already built. Their `from_row` needs more than the
            # avatar map now — a like count and two per-viewer answers, one of
            # which (`can_delete`) is a policy the service owns — so building
            # them here would mean moving that policy into a schema, or handing
            # this method a viewer to re-decide it with. The service assembles
            # them (`FeedbackService.comments_out`) and this only carries them.
            #
            # Notes still go through their own `from_row` for the reason the card
            # does: the author's face is not on their row, and this is the one
            # place that already has the page-wide map.
            thread=list(thread or []),
            thread_next_cursor=thread_next_cursor,
            notes=[NoteOut.from_row(x, avatars=avatars) for x in notes or []],
            can_delete=can_delete,
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
    #: 「热门」这一栏的规则，三个数——为什么是三个而不是一个，见
    #: `repositories.HOT_SCORE`：门槛、半衰期、补足条数各管一件事，而客户端要能把这
    #: 一栏**说给人听**（「两周前的一票算今天半票 · 至少 5 条」）。规则留在一个数字
    #: 里的话，读者看到的是一栏他无法解释的排序。
    #:
    #: 这三个数**不是**给客户端自己算热度的：排序和筛选都在服务端（`hot_score()`），
    #: 客户端拿到的是已经排好的行。它们只用来把规则写出来——客户端算第二遍的话，
    #: 屏幕上就会出现两套热度。
    hot_score: float
    hot_half_life_days: float
    hot_min_items: int
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


class CommentLikeOut(BaseModel):
    """Result of POST/DELETE a comment like — `SupportOut`, one level down.

    Same shape and the same rule (the count **after** the write, never a delta),
    with `liked` in place of `supported` because the client is answering a
    different question: 「我点过这条回复没有」, not 「我顶过这条反馈没有」. One
    field named `supported` on two different subjects is how a renderer ends up
    binding the wrong one.
    """

    count: int
    liked: bool
