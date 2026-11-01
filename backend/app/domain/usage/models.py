"""Resource usage — LLM token/cost accounting (spec §9.1 三级可见性, §10.2).

One row per agent turn. Aggregated at topic / project / Space levels so usage is
visible at each level (话题→项目→机构).
"""

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk

# The registry `conversation_id` points at: mapped wherever this is, so the
# foreign key resolves in a process that never imports `app.models`.
from app.domain.conversation.models import Conversation  # noqa: F401


class GrantSource(StrEnum):
    """Where a credit pack came from; it decides when the pack lapses and where
    it falls in the order a charge drains packs in (``usage.balance``)."""

    # The team's plan, one per period; lapses when the period ends.
    PLAN_PERIOD = "plan_period"
    # A 赛题's 资源包 for one project; spendable only there.
    TASK_EARMARK = "task_earmark"
    PURCHASE = "purchase"
    ADMIN_GRANT = "admin_grant"
    # A beta, feedback or invitation reward (``incentives.CreditIncentives``).
    REWARD = "reward"


class Plan(Timestamps, Base):
    """A credit plan, a record an administrator edits rather than code (#2397).

    Every team is on one (``team.plan_key``). A plan bills one of two ways: it
    issues ``credits_per_period`` credits to each of its teams every month, or
    it issues nothing and lets them spend up to a cap inside each of its time
    ``windows``. It also says which model tiers its teams may use.
    ``unlimited`` plans issue nothing and refuse nothing.
    """

    __tablename__ = "plans"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    # Who may be put on it: "personal" teams, shared "team"s, or "both".
    audience: Mapped[str] = mapped_column(String(16), default="both")
    # The plan's pack per period; NULL issues none.
    credits_per_period: Mapped[float | None] = mapped_column(Float, nullable=True)
    period: Mapped[str] = mapped_column(String(16), default="month")
    # A plan that issues no pack: at most ``credits`` within each window.
    # ``{"hours": 5, "credits": 100}`` starts at a team's first call and resets
    # that many hours later; ``{"calendar": "week" | "month", "credits": 100}``
    # resets every Monday or every first of the month (``usage.windows``).
    windows: Mapped[list] = mapped_column(JSON, default=list)
    # The model tiers its teams may use, the subscription (Claude) models
    # included; NULL is every tier.
    model_tiers: Mapped[list | None] = mapped_column(JSON, nullable=True)
    unlimited: Mapped[bool] = mapped_column(Boolean, default=False)
    # Only an administrator can put a team on it, and only the console lists it.
    admin_only: Mapped[bool] = mapped_column(Boolean, default=False)
    # Where it stands among plans, lowest first: the console lists them in this
    # order, and a model the team's plan does not allow names the first plan
    # that does. Plans carry no price yet; this is the administrator's order.
    rank: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class PlanWindowUse(Base):
    """What a team has spent inside one of its plan's time windows this round.

    One row per team and window (``window`` is ``"5h"``, ``"week"`` or
    ``"month"``). A round that has run out is restarted by the next charge, so
    an old row reads as an empty window until then. Only spending the plan
    covers counts: an earmark or bought credits never fill a window.
    """

    __tablename__ = "plan_window_use"

    team_id: Mapped[int] = mapped_column(
        ForeignKey("team.id", ondelete="CASCADE"), primary_key=True
    )
    window: Mapped[str] = mapped_column(String(16), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    credits_used: Mapped[float] = mapped_column(Float, default=0.0)


class CreditReward(Timestamps, Base):
    """One campaign participation, accepted feedback or invitation step can
    issue one pack. ``points`` is the contribution score the same event earns:
    it only ever adds up, and it is still recorded when no credits are paid.
    Nobody is shown points yet: the team took them off every page until they
    lead to something (a ranking, a perk), and keeps recording them so that
    day starts from the full history."""

    __tablename__ = "credit_rewards"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    team_id: Mapped[int] = mapped_column(
        ForeignKey(
            "team.id", ondelete="CASCADE", name="fk_credit_rewards_team_id_team"
        ),
        index=True,
    )
    credits: Mapped[int] = mapped_column(Integer)
    points: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    reason: Mapped[str] = mapped_column(String(128))
    actor_handle: Mapped[str] = mapped_column(String(64))


class CreditRequest(UuidPk, Timestamps, Base):
    """A team asks for a plan or top-up; no payment occurs on submission."""

    __tablename__ = "credit_requests"
    __table_args__ = (
        Index(
            "uq_credit_requests_pending",
            "team_id",
            "kind",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    team_id: Mapped[int] = mapped_column(
        ForeignKey(
            "team.id", ondelete="CASCADE", name="fk_credit_requests_team_id_team"
        ),
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(16))
    credits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    requester_handle: Mapped[str] = mapped_column(String(64))
    handled_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class OverseasModelApproval(Timestamps, Base):
    """A team a platform administrator has let use overseas models (Claude,
    GPT, Gemini). A team without a row may not, whatever its plan allows; a
    person is let in through their personal team. Withdrawing deletes the row,
    and ``credit_admin_audit`` keeps who did either and when."""

    __tablename__ = "overseas_model_approvals"

    team_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "team.id",
            ondelete="CASCADE",
            name="fk_overseas_model_approvals_team_id_team",
        ),
        primary_key=True,
    )
    approved_by: Mapped[str] = mapped_column(String(64))


class CreditAdminAudit(UuidPk, Timestamps, Base):
    """Every change an administrator makes to plans and credit packs: who, to
    what, and what it was before and after."""

    __tablename__ = "credit_admin_audit"
    __table_args__ = (
        Index("ix_credit_admin_audit_created_at", text("created_at DESC")),
    )

    actor_handle: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(32))
    target: Mapped[str] = mapped_column(String(128))
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class ComputeGrant(UuidPk, Timestamps, Base):
    """A credit pack: a balance that spending draws down.

    Every pack belongs to a team, never to a person inside one; a person's own
    credits are packs on their personal team. ``project_id`` narrows a pack to
    one project of the team (a task's earmark). ``expires_at`` is when it
    lapses; NULL never does.
    """

    __tablename__ = "compute_grants"
    __table_args__ = (
        # One plan pack per team per period, so issuing it is an insert that a
        # concurrent first request can lose without writing a second one.
        Index(
            "uq_compute_grants_plan_period",
            "team_id",
            "period_start",
            unique=True,
            postgresql_where=text("source = 'plan_period'"),
        ),
        CheckConstraint(
            "source IN ('plan_period', 'task_earmark', 'purchase', 'admin_grant', "
            "'reward')",
            name="ck_compute_grants_source",
        ),
    )

    team_id: Mapped[int] = mapped_column(
        ForeignKey("team.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    source: Mapped[str] = mapped_column(String(32))
    # The 赛题 whose 项目集 funded this grant (#370). An int, and deliberately
    # NOT a foreign key: the credits were granted, so the audit trail has to
    # survive the 赛题 being deleted.
    source_task_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # The first day of the period a plan pack is for, in the platform's timezone.
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Why an administrator issued it (a contract, a refund); shown only to
    # administrators.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    credits_total: Mapped[float] = mapped_column(Float)
    credits_used: Mapped[float] = mapped_column(Float, default=0.0)


#: The ``route`` of a usage row that charges cloud compute rather than a model
#: call (``usage.compute``): no tokens, and no part of any count of calls.
COMPUTE_ROUTE = "compute"
#: The ``route`` of a usage row that charges web searches the platform asked
#: for (`cheese_web(action=search)`): like a compute charge, credits and no call. The
#: searches are made on the caller's gateway key and drained with its model
#: calls (``agent.gateway_spend``), which lands the gateway's search line
#: (``GATEWAY_SEARCH_MODEL``) under this route and ``SEARCH_KIND``.
SEARCH_ROUTE = "search"
SEARCH_KIND = "web_search"
#: The name the gateway gives its DeepSeek search tool's spend
#: (deploy/gateway/deepseek_search.py, ``search_tools`` in config.yaml).
GATEWAY_SEARCH_MODEL = "deepseek/search"
#: The routes of rows that charge credits for something other than a model
#: call.
NOT_MODEL_CALLS = (COMPUTE_ROUTE, SEARCH_ROUTE)


class ProjectCreditBudget(Timestamps, Base):
    """A project's lifetime spending cap, independent of its team's balance."""

    __tablename__ = "project_credit_budgets"
    __table_args__ = (
        CheckConstraint(
            "credits_limit IS NULL OR "
            "(credits_limit >= 0 AND credits_limit < 'Infinity'::float8)",
            name="ck_project_credit_budgets_limit",
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "projects.id",
            ondelete="CASCADE",
            name="fk_project_credit_budgets_project_id_projects",
        ),
        primary_key=True,
    )
    credits_limit: Mapped[float | None] = mapped_column(Float, nullable=True)
    since: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MemberCreditBudget(Timestamps, Base):
    """A member's task spending cap in one project; this issues no credits."""

    __tablename__ = "member_credit_budgets"
    __table_args__ = (
        CheckConstraint(
            "credits_limit >= 0 AND credits_limit < 'Infinity'::float8",
            name="ck_member_credit_budgets_limit",
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "projects.id",
            ondelete="CASCADE",
            name="fk_member_credit_budgets_project_id_projects",
        ),
        primary_key=True,
    )
    owner_handle: Mapped[str] = mapped_column(String(64), primary_key=True)
    credits_limit: Mapped[float] = mapped_column(Float)


class WorkCreditOwner(Base):
    """The task owner when a turn opened, kept for delayed usage settlement."""

    __tablename__ = "work_credit_owners"

    work_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "projects.id",
            ondelete="CASCADE",
            name="fk_work_credit_owners_project_id_projects",
        ),
        index=True,
    )
    # Kept after the task is deleted so late provider receipts retain their owner.
    conversation_id: Mapped[uuid.UUID] = mapped_column(index=True)
    owner_handle: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TaskGatewayAccount(Base):
    """One gateway meter for a task's human budget holder, retained after deletion."""

    __tablename__ = "task_gateway_accounts"

    conversation_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    owner_handle: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "projects.id",
            ondelete="CASCADE",
            name="fk_task_gateway_accounts_project_id_projects",
        ),
        index=True,
    )
    gateway_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    checkpoint: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    budget_usd: Mapped[float | None] = mapped_column(Float, nullable=True)


class MachineGatewayAccount(Base):
    """The gateway key a conversation's sandboxes call models on, for one
    budget holder (``""`` when the project pays): apart from the session's own
    key, with a daily cap on the gateway, so a machine whose owner relays its
    platform socket spends that much a day at most. Kept, like the task's, so
    late spend is still charged."""

    __tablename__ = "machine_gateway_accounts"

    conversation_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    owner_handle: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "projects.id",
            ondelete="CASCADE",
            name="fk_machine_gateway_accounts_project_id_projects",
        ),
        index=True,
    )
    gateway_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    checkpoint: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    daily_budget_usd: Mapped[float | None] = mapped_column(Float, nullable=True)


#: The usage rows a project's gateway key spent: `ix_resource_usage_gateway_spend`
#: covers exactly these, so the predicate is written once, as a literal.
GATEWAY_ROWS = text("route = 'gateway'")


class ResourceUsage(UuidPk, Timestamps, Base):
    __tablename__ = "resource_usage"
    #: 平台看板按天聚合这张表：`created_at` 的范围扫。等值的四条索引
    #: （`project_id` / `conversation_id` / `turn_id`）一条都服务不了它——
    #: 这是全仓增长最快的一张表，没有它就是每次看板全表顺序扫。迁移见
    #: `a9c4e7f12b60`。
    __table_args__ = (
        Index("ix_resource_usage_created_at", "created_at"),
        # A person's spend in a month: the personal usage page reads it.
        Index("ix_resource_usage_user_created_at", "user_id", "created_at"),
        # A team's spend in a month, by day: the usage pages read it.
        Index(
            "ix_resource_usage_team_created_at",
            "team_id",
            "created_at",
            postgresql_include=["credits"],
        ),
        Index(
            "ix_resource_usage_project_owner_created",
            "project_id",
            "credit_owner_handle",
            "created_at",
            postgresql_include=["credits"],
        ),
        # What a project's gateway key has spent ever, read on every model
        # admission (`UsageRepository.gateway_spend`): answered from the index
        # alone. Query and index share `GATEWAY_ROWS`, a literal, so the planner
        # can prove the predicate for a prepared statement as well.
        Index(
            "ix_resource_usage_gateway_spend",
            "project_id",
            postgresql_include=["cost_usd"],
            postgresql_where=GATEWAY_ROWS,
        ),
    )

    # NULL when the spend happened outside any project (see ``user_id``).
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Who asked, for spend outside a project; their personal team paid. NULL
    # inside a project, whose spend is not split by person (#394).
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=True
    )
    # A task's budget holder at execution time. Public project work and older
    # rows stay NULL; changing a task's owner never rewrites settled spending.
    credit_owner_handle: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The conversation the spend happened in, a room or a task; NULL when it
    # cannot be attributed at all.
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "conversations.id",
            ondelete="SET NULL",
            name="fk_resource_usage_conversation_id_conversations",
        ),
        nullable=True,
        index=True,
    )
    # The human message or platform work id this spend belongs to. One attributed
    # unit can write more than one row because the metering proxy logs every
    # /v1/messages call and the gateway can land a deferred backfill. The column
    # name stays for storage and protocol compatibility. NULL means the supply
    # cannot be attributed; each such row remains one unit in aggregate reports.
    turn_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    model: Mapped[str] = mapped_column(String(64), default="")
    # Every prompt token, cached ones included; the cache columns are the
    # shares of it read from and written to the provider's cache, and of the
    # writes, those to the one-hour cache. The gateway's drain does not split
    # cache writes by lifetime, so its one-hour column stays 0.
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0"
    )
    cache_write_tokens: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0"
    )
    cache_write_1h_tokens: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0"
    )
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    total_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    kind: Mapped[str] = mapped_column(String(24), default="chat")
    # The supply the traffic actually took (issue #218): "gateway" (LiteLLM),
    # "subscription" (metering proxy), "native" (profile-pinned credentials);
    # "compute" for a charge of cloud compute (``COMPUTE_ROUTE``), "search"
    # for web searches the platform asked the gateway for (``SEARCH_ROUTE``).
    # "" on rows that predate the column.
    route: Mapped[str] = mapped_column(String(16), default="")
    # The team that paid, and the credits charged for this row (#2397). Every
    # new row names one: the platform's own calls for a project are the
    # project's (``Ledger.record_project_call``). Rows with no team are older:
    # written before the team was recorded (they carry a project or a person),
    # or calls the platform once paid for itself, ``kind`` naming the job.
    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("team.id", ondelete="CASCADE"), nullable=True
    )
    credits: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    # How many model calls the row covers. One per row, except a gateway drain,
    # which lands each model's calls since the last drain as one row, so the
    # platform's count of calls is this column's sum, not the number of rows.
    calls: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


def call_count() -> Any:
    """How many model calls the rows cover: the sum of ``calls``, not the
    number of rows, because a gateway drain lands many calls as one row."""
    return func.coalesce(func.sum(ResourceUsage.calls), 0)


class IngestCheckpoint(Base):
    """Exactly-once progress marker for an append-only usage log (issue #218).

    One row per source file. ``byte_offset`` is how far ingestion has consumed;
    ``fingerprint`` hashes the file's first bytes so a rotated/replaced file
    reads as a new generation and restarts from zero — the alternative is
    silently skipping (offset past a shorter file) or double-billing (offset
    reset against the same file). Committed in the SAME transaction as the rows
    it covers, which is the whole exactly-once argument.
    """

    __tablename__ = "ingest_checkpoints"

    source: Mapped[str] = mapped_column(String(128), primary_key=True)
    byte_offset: Mapped[int] = mapped_column(BigInteger, default=0)
    fingerprint: Mapped[str] = mapped_column(String(64), default="")


class ComputeRun(UuidPk, Timestamps, Base):
    """One stretch of cloud compute running for a project, from start to stop:
    a cloud sandbox (``kind`` ``sandbox``) or a whole cloud VM (``vm``).

    ``subject`` names what ran, a sandbox home's id or a VM's own id; one run
    of it is open at a time. ``billed_until`` is how far the run has been
    charged, in whole minutes from ``started_at``; a run is settled once that
    has reached ``ended_at`` (``usage.compute``).
    """

    __tablename__ = "compute_runs"
    __table_args__ = (
        Index(
            "uq_compute_runs_open_subject",
            "subject",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
        ),
        # What the settlement reads: runs still running, and ended ones not
        # charged to their end yet.
        Index(
            "ix_compute_runs_unsettled",
            "billed_until",
            postgresql_where=text("ended_at IS NULL OR billed_until < ended_at"),
        ),
        CheckConstraint("kind IN ('sandbox', 'vm')", name="ck_compute_runs_kind"),
    )

    kind: Mapped[str] = mapped_column(String(16))
    # The price list entry: ``sandbox`` for a sandbox, the VM's spec name.
    spec: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(64))
    # None for a run of a conversation in no project: its person pays.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=True
    )
    # The conversation the sandbox ran for.
    topic_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "conversations.id",
            ondelete="SET NULL",
            name="fk_compute_runs_topic_id_conversations",
        ),
        index=True,
        nullable=True,
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    billed_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Credits charged for it so far.
    credits: Mapped[float] = mapped_column(Float, default=0.0)
