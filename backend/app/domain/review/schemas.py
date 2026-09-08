"""Accept card request/response schemas (Pydantic v2) — spec §4.4, §6.3."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.review.models import AcceptStatus


class AcceptCardCreate(BaseModel):
    reviewer_handle: str = Field(min_length=1, max_length=64)
    routing_reason: str = ""
    # What the change IS, in Conventional Commits form — becomes the PR title
    # and the squash commit subject. REQUIRED since 2026-08-17, but enforced in
    # review/services.py rather than here: a Pydantic-required field answers
    # 422 with pydantic's own wording, and the thing that has to reach the
    # filer is a sentence teaching them how to write one. Typed optional only
    # so that message — not `Field required` — is what comes back.
    change_subject: str | None = Field(default=None, max_length=255)
    change_body: str | None = None
    # 这批交付是哪几条活干出来的 (#189) — the room naming the work whose code is
    # actually in this change, which is the one thing about a delivery the
    # platform cannot see for itself. Becomes `Cheese-Task:` in permanent
    # history, so an id that does not belong to this room is refused rather
    # than written. Appearing in an earlier delivery is not a reason to refuse
    # it: one piece of work can be delivered, keep being written, and land
    # again in the next batch — it really did write both.
    #
    # Empty by default and empty is allowed: nothing is inferred from silence,
    # because every inference available ("on the delivering tree", "not yet
    # claimed") proves only that a task row exists — not that its code is here.
    # An undeclared delivery lands with no `Cheese-Task:` line, which is honest.
    task_ids: list[uuid.UUID] = Field(default_factory=list)


#: 合的是**人看到的**那个 commit：每个会触发合并的写入口都带上前端渲染这张卡
#: 时卡面显示的 head sha（`merge_state.head_sha`）。它不是给服务端「用哪个 sha
#: 合并」的建议，而是给它「我看的是哪一版」的声明——服务端拿它和卡当前的
#: `pr_head_sha` 对；不一致就是「你看的那版已经不在了」，请求被拒、人重新看过
#: 再点。没有它的话，轮询器在渲染和点击之间把卡刷到新 head，点下去合的就是一
#: 段没有人看过的代码。None 是合法值：卡还没被轮询器镜像过 head（刚递的卡）、
#: 或者根本不骑 PR（平台 lane）时，卡面显示的就是「没有 sha」。
_SEEN_HEAD_FIELD = Field(default=None, max_length=64)


class AcceptDecision(BaseModel):
    decided_by: str = Field(min_length=1, max_length=64)
    head_sha: str | None = _SEEN_HEAD_FIELD


class RejectDecision(BaseModel):
    decided_by: str = Field(min_length=1, max_length=64)
    note: str = ""


class VoidDecision(BaseModel):
    """人工作废 (2026-08-11). No `decided_by`, unlike the other decisions here:
    作废 is an authorization action, so the actor comes from the session token
    only — a caller must never be able to name someone else as the one who
    did it. Same shape as 改验收人 (`reassign`)."""

    note: str = Field(default="", max_length=2000)


class ForceMergeDecision(BaseModel):
    """人工放行。No `decided_by`, same reason as 作废: this is an authorization
    action and the signature is the entire point — the actor comes from the
    session token only, never from the body, or "谁明知红仍合并" would be
    whatever the caller typed."""

    reason: str = Field(default="", max_length=2000)
    head_sha: str | None = _SEEN_HEAD_FIELD


class AutoMergeDecision(BaseModel):
    """绿了自动合 (#718) 的开关。Actor 同样只来自 session token：布防等于提前
    采纳，布防人是谁必须由平台认定。"""

    enabled: bool
    head_sha: str | None = _SEEN_HEAD_FIELD


class ApprovalCreate(BaseModel):
    approver_handle: str = Field(min_length=1, max_length=64)


class AcceptCardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    topic_id: uuid.UUID
    reviewer_handle: str
    routing_reason: str
    # The commit this card will become, as filed — so the reviewer can see the
    # subject that is about to enter the project's history BEFORE accepting,
    # which is the last moment anyone can object to it.
    change_subject: str | None = None
    change_body: str | None = None
    status: AcceptStatus
    decided_by: str | None
    decided_at: datetime | None
    note: str
    #: 这条 note 是「停住了」(error) 还是「还在走」(info)，空 note 是 None。
    #: 服务端从卡的状态码算好下发 (domain/review/notes.py)，浏览器只把它画成
    #: 颜色，不再去读文案开头那个字符。
    note_level: str | None = None
    created_at: datetime
    # 机器闸门 (eval C2): when the check_command started / passed. `started` is
    # NULL on a card whose gate never actually ran — see models.AcceptCard.
    gate_started_at: datetime | None = None
    gate_passed_at: datetime | None = None
    gate_output: str = ""
    # PR-based accept (#188 §5.1 → #718): the real GitHub PR this card rides
    # on. pr_head_sha is the head the card shows — the commit an accept click
    # will merge; see models.AcceptCard.
    pr_number: int | None = None
    pr_url: str | None = None
    pr_repo: str | None = None
    pr_head_sha: str | None = None
    pr_merged_at: datetime | None = None
    # 主分支保护 (spec §4.4): votes so far / votes needed. Enriched by the
    # service (approvals live in their own table; the requirement is a project
    # setting), so plain model_validate(card) keeps the defaults.
    approvals: list[str] = []
    approvals_required: int = 1
