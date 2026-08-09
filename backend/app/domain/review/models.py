"""Accept card model — spec §4.4, §6.3, eval C5.

When a topic's main deliverable is ready, 芝士 hands the accept card to a
specific person ("等 XX 验收"), not a broadcast. Hard rule (spec §4.4): in
collaborative mode AI cannot accept its own work. Accept is recorded by name and
revocable; accepting = merge + archive the topic.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class AcceptStatus(enum.StrEnum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"
    revoked = "revoked"
    # 采纳时 merge 冲突：话题不归档、卡片进入此状态，芝士被派去解决冲突，
    # 解决后由人重试采纳（spec §6.3 冲突处理：芝士先尝试解决）。
    conflict = "conflict"
    # 机器闸门 (spec §4.4/§9, eval C2): the project's check_command is running
    # in the topic's workspace; the card reaches the reviewer only when green.
    pending_gate = "pending_gate"
    # 检查红了：卡片作废（不递给验收人），芝士收到系统 nudge 去修，修完重新递卡。
    gate_failed = "gate_failed"
    # 两阶段采纳 (PR迭代式，2026-08-09)：人点了采纳，批准人有可用的已连接
    # GitHub token，PR 已推送/开出，话题不归档，容器不停。`pr_merged_at` on the
    # card distinguishes still-waiting-on-PR-checks (None) from
    # merged-waiting-on-deploy (set) — both live under this one status so a
    # reviewer/API consumer sees one "still iterating" state, not two.
    pr_open = "pr_open"


class AcceptCard(UuidPk, Timestamps, Base):
    __tablename__ = "accept_cards"

    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True
    )
    # Routed reviewer (spec C5): the specific person asked to accept.
    reviewer_handle: Mapped[str] = mapped_column(String(64), index=True)
    # Why this reviewer was suggested (最懂/没参与/有空), for transparency.
    routing_reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[AcceptStatus] = mapped_column(
        Enum(AcceptStatus, native_enum=False, length=16),
        default=AcceptStatus.pending,
    )
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    note: Mapped[str] = mapped_column(Text, default="")
    # 机器闸门 (eval C2): when the project's check_command passed for this card.
    gate_passed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Tail of the check output (green or red) — full output is in the gate log.
    gate_output: Mapped[str] = mapped_column(Text, default="", server_default="")
    # PR-based accept (#188 §5.1, extended 2026-08-09 by 两阶段采纳/PR迭代式):
    # the real GitHub PR this card rides on. `pr_number`/`pr_url` are set
    # either by pr_publish.py (fire-and-forget on a card turning pending, the
    # original #188 §5.1 flow, off by default behind `settings.accept_via_pr`)
    # or by AcceptService._accept_via_pr (the two-phase flow, triggered when a
    # human clicks accept and the approver has a usable connected GitHub
    # token — see review/services.py). Either way, a card WITH a pr_number
    # rides a PR; one without falls back to the local merge + push_back path
    # — every card is self-describing, so flag flips and GitHub outages never
    # strand one.
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 两阶段采纳 (PR迭代式) only, below: which repo (#192 project_git_installations,
    # not necessarily the same as the `upstream` git remote pr_publish.py
    # resolves from), and the polling state while status == pr_open.
    pr_repo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Head commit of the pushed PR branch — what check-runs/workflow-runs are
    # queried against (a fresh push moves this, so polling never checks a stale
    # commit's status after 芝士 pushes a fix).
    pr_head_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # None: still waiting on the PR's own CI. Set: PR merged, now waiting on
    # the deploy workflow it triggered before the topic can finally archive.
    pr_merged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AcceptApproval(UuidPk, Timestamps, Base):
    """主分支保护 (spec §4.4): one human vote toward a card's accept.

    A card needs `approvals_required` (Project.settings) distinct approvals
    before accept actually merges. Votes are history — revoking an accept does
    not clear them.
    """

    __tablename__ = "accept_approvals"
    __table_args__ = (
        UniqueConstraint("card_id", "approver_handle", name="uq_accept_approval"),
    )

    card_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accept_cards.id", ondelete="CASCADE"), index=True
    )
    approver_handle: Mapped[str] = mapped_column(String(64))
