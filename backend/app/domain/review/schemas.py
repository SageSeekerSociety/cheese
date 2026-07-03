"""Accept card request/response schemas (Pydantic v2) — spec §4.4, §6.3."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.review.models import AcceptStatus


class AcceptCardCreate(BaseModel):
    reviewer_handle: str = Field(min_length=1, max_length=64)
    routing_reason: str = ""


class AcceptDecision(BaseModel):
    decided_by: str = Field(min_length=1, max_length=64)


class RejectDecision(BaseModel):
    decided_by: str = Field(min_length=1, max_length=64)
    note: str = ""


class ApprovalCreate(BaseModel):
    approver_handle: str = Field(min_length=1, max_length=64)


class AcceptCardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    topic_id: uuid.UUID
    reviewer_handle: str
    routing_reason: str
    status: AcceptStatus
    decided_by: str | None
    decided_at: datetime | None
    note: str
    created_at: datetime
    # 机器闸门 (eval C2): set when the project's check_command passed.
    gate_passed_at: datetime | None = None
    gate_output: str = ""
    # 主分支保护 (spec §4.4): votes so far / votes needed. Enriched by the
    # service (approvals live in their own table; the requirement is a project
    # setting), so plain model_validate(card) keeps the defaults.
    approvals: list[str] = []
    approvals_required: int = 1
