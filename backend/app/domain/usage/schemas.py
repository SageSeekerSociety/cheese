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


class CreditRewardItemOut(BaseModel):
    kind: Literal["beta", "feedback", "invite"]
    credits: int
    created_at: datetime


class CreditRewardsOut(BaseModel):
    feedback_count: int
    feedback_credits: int
    invite_credits: int
    items: list[CreditRewardItemOut]


class PublishedCreditPlanOut(BaseModel):
    key: str
    name: str
    audience: Literal["personal", "team", "both"]
    credits_per_period: float | None
    period: str
    windows: list[dict]
    model_tiers: list[str] | None
    unlimited: bool
    models: list[str]


class PublishedCreditPlansOut(BaseModel):
    plans: list[PublishedCreditPlanOut]


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


class InviteeOut(BaseModel):
    handle: str
    nickname: str | None
    avatar_id: int | None
    registered_at: datetime
    #: registered → onboarded (has a project) → activated (accepted a delivery
    #: in time, both sides paid); lapsed when the seven days ran out first.
    stage: Literal["registered", "onboarded", "activated", "lapsed"]
    #: What the inviter was paid for this friend: 0 until activated, and 0
    #: past the monthly cap.
    credits: int


class MyInvitesOut(BaseModel):
    code: str
    max_uses: int
    use_count: int
    #: Whether the owner's own first accepted delivery has added the extra uses.
    unlocked: bool
    unlock_uses: int
    invite_credits: int
    invitees: list[InviteeOut]
