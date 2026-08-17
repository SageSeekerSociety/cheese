"""结论卡 request/response schemas (Pydantic v2)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.conclusion.models import ConclusionStatus


class ConclusionSettleIn(BaseModel):
    """采信 —— 零成本的那一边，所以什么都不用填（`decided_by` 可选，缺省记
    平台自己）。打回才要付理由和一整轮。"""

    decided_by: str | None = Field(default=None, max_length=64)


class NeedEvidenceIn(BaseModel):
    decided_by: str | None = Field(default=None, max_length=64)
    reason: str = Field(min_length=1)
    # 阶段二：卡面 uncertainties 里那条 blocking 项的 id。阶段一的卡没有结构化
    # 栏位可引用，服务层会放行 —— 见 services._check_evidence_anchor。
    blocking_ref: str | None = None


class EscalateIn(BaseModel):
    decided_by: str | None = Field(default=None, max_length=64)
    reason: str = Field(min_length=1)


class ConclusionCardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    topic_id: uuid.UUID
    receiver_topic_id: uuid.UUID
    conclusion: str
    status: ConclusionStatus
    settled_by: str | None
    settled_at: datetime | None
    settle_reason: str
    blocking_ref: str | None
    returned_count: int
    digest_deadline_at: datetime
    created_at: datetime
