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


class AcceptDecision(BaseModel):
    decided_by: str = Field(min_length=1, max_length=64)


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
    """人工放行 (App 采纳等 CI 再合)。No `decided_by`, same reason as 作废: this
    is an authorization action and the signature is the entire point — the
    actor comes from the session token only, never from the body, or "谁明知红
    仍合并" would be whatever the caller typed."""

    reason: str = Field(default="", max_length=2000)


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
    # PR-based accept (#188 §5.1): the real GitHub PR this card rides on.
    # pr_repo/pr_head_sha/pr_merged_at are 两阶段采纳 (PR迭代式) only, populated
    # while status == pr_open — see models.AcceptCard for the full story.
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
