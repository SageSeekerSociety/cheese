"""Credit budgets, rewards and purchase enquiry responses."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class MemberCreditBudgetOut(BaseModel):
    name: str | None = None
    credits_limit: float | None
    credits_used: float


class ProjectCreditBudgetOut(BaseModel):
    credits_limit: float | None
    credits_used: float
    credits_remaining: float | None
    public_credits: float
    unattributed_credits: float
    members: dict[str, MemberCreditBudgetOut]
    since: datetime


class BetaCreditsOut(BaseModel):
    credits: int
    claimed: bool
    feedback_credits: int


class CreditRewardOut(BaseModel):
    credits: int
    issued: bool


class CreditRequestOut(BaseModel):
    id: str
    team_id: int
    team_name: str | None = None
    kind: Literal["top_up", "team_plan"]
    credits: int | None
    status: Literal["pending", "completed", "declined"]
    requester_handle: str
    created_at: datetime


class CreditRequestsOut(BaseModel):
    items: list[CreditRequestOut]
