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
    # 闸门没跑成（2026-08-11）：检查本身没能在门禁容器里跑起来（工具链缺失/
    # 装不上、Docker 起不来），所以它对代码**没有结论**。既不是绿也不是红：
    # 卡照样不递给验收人，但话术和卡面都要说"没跑成"而不是"没通过"——把它
    # 当绿放行，正是这个状态存在的原因（见 .claude/scripts/check.sh 的 exit 2）。
    gate_blocked = "gate_blocked"
    # 两阶段采纳 (PR迭代式，2026-08-09)：人点了采纳，批准人有可用的已连接
    # GitHub token，PR 已推送/开出，话题不归档，容器不停。`pr_merged_at` on the
    # card distinguishes still-waiting-on-PR-checks (None) from
    # merged-waiting-on-deploy (set) — both live under this one status so a
    # reviewer/API consumer sees one "still iterating" state, not two.
    pr_open = "pr_open"


class GateOutcome(enum.StrEnum):
    """What a quality-gate run actually established (2026-08-11).

    Not a bool: "the check ran and disliked the code" and "the check never ran"
    are different facts, and collapsing them is how a gate ends up green on an
    environment where nothing but lint could start. `check_command` reports the
    third one with exit code 2 (see .claude/scripts/check.sh --strict).
    """

    passed = "passed"
    failed = "failed"
    blocked = "blocked"


class AcceptCard(UuidPk, Timestamps, Base):
    __tablename__ = "accept_cards"

    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True
    )
    # Routed reviewer (spec C5): the specific person asked to accept.
    reviewer_handle: Mapped[str] = mapped_column(String(64), index=True)
    # Why this reviewer was suggested (最懂/没参与/有空), for transparency.
    routing_reason: Mapped[str] = mapped_column(Text, default="")
    # What this topic CHANGED, in the words of whoever did the work — the one
    # description that survives into permanent history. `change_subject` is a
    # Conventional Commits subject (`fix(api): …`, imperative, ≤72 chars) and
    # `change_body` is the why. They become the PR title/body AND the squash
    # commit that lands on the default branch, so the project's git log stops
    # reading "采纳 <话题标题> (#213)" — a room name, not a change description.
    # NULL on cards filed without them (and on every card that predates the
    # columns): review/services.py falls back to the topic title.
    change_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    change_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[AcceptStatus] = mapped_column(
        Enum(AcceptStatus, native_enum=False, length=16),
        default=AcceptStatus.pending,
    )
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    note: Mapped[str] = mapped_column(Text, default="")
    # 机器闸门 (eval C2): when the project's check_command STARTED running for
    # this card. Deliberately not "when the card was filed" (that is
    # `created_at`) — the gap between the two is queueing + worktree
    # preparation, and a `pending_gate` card with this still NULL never got as
    # far as running the check at all. See review/gate_sweep.py, which uses
    # COALESCE(gate_started_at, created_at) as its clock.
    gate_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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
    # 人类授权动作前移 (2026-08-10): the commit the human actually authorized —
    # frozen at the moment they clicked, while `pr_head_sha` keeps moving with
    # every 芝士 fix pushed onto the PR afterwards. THE reason this is a column
    # and not derived: the safety valve is baseline-relative by definition
    # ("approve 之后 head 又动，且新 diff 超出授权范围"), and after two pushes
    # nothing else on the card, on GitHub, or in the local repo still says what
    # the human saw. Comparing each push against the PREVIOUS one instead would
    # forget drift as soon as a benign push followed a risky one.
    # NULL = a card from before this existed (or one that never rode a PR):
    # 在途的 pr_open 卡不能被打断, so the poller adopts the current head as the
    # baseline on its first tick rather than blocking retroactively.
    pr_authorized_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
