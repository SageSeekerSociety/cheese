"""结论卡 (conclusion card) — the receipt a sub-topic's conclusion gets.

`conclude` used to be "pour text into the parent's living doc": it DID leave a
trace (a message, a doc section, a change-alert) but there was no receipt, no
status and no idempotency — the sub-topic stayed `active` forever and every
re-conclude appended another section to the parent's doc.

A conclusion card fixes the receipt/status half of that. Its receiver is a
TOPIC, not a person (`receiver_topic_id`):采信 a research conclusion needs
nobody's authority, so it must not spend anybody's attention. The default is
**accept** — the card settles itself when the parent's digest turn ends, so
"默认采信" happens whether or not anyone remembers to click.

Deliberately its OWN table, not a row in `accept_cards`: that table carries the
accept-card state machine (gate/PR/approvals) and is swept by the PR poller by
status alone, so a conclusion card living there would be picked up by machinery
that knows nothing about it.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class ConclusionStatus(enum.StrEnum):
    open = "open"
    # 默认终态：父话题那一轮结束（或 30 分钟绝对超时）时自动到这里。
    accepted = "accepted"
    # 补证据：唯一能回到 open 的分支。命名刻意避开「驳回/rejected」——
    # 卡面的动词直接影响 AI 的行为倾向，这条是设计要求，不是措辞偏好。
    returned = "returned"
    # 转人向：这个结论需要以某人的名义做出去，超出「父话题信不信」的范围。
    escalated = "escalated"
    # 结算前子话题又 conclude 了一次：老卡作废（顺带解决幂等）。
    superseded = "superseded"


#: 结算态 —— 不再需要任何人/任何一轮去处理的状态。
SETTLED_STATES = (
    ConclusionStatus.accepted,
    ConclusionStatus.escalated,
    ConclusionStatus.superseded,
)

#: 每张卡最多打回 1 次；2 是硬上限，用完只剩采信或升级 (设计 §二 机制③)。
MAX_RETURNS = 1
HARD_MAX_RETURNS = 2

#: 绝对超时：父话题那一轮若根本没跑起来（排队、崩溃、被掐），卡也不能永远挂着。
DIGEST_TIMEOUT_MINUTES = 30

#: 归档待办 —— 结论被采信了，但这个子话题（或归档会一起带走的后代）还挂着一张
#: 没人决议的验收卡，所以归档推迟到卡有结果之后。
#:
#: 为什么是 `settle_reason` 而不是一列：采信的 reason 一向是空串（零成本是它的
#: 重点），所以这个哨兵值不会跟任何既有内容撞；这一栏记的本来就是"这张卡结算时
#: 发生了什么"，推迟归档正是那件事。而且它是**一次性**的：待办消解时被改写成
#: 下面那个值，扫描就再也选不中这张卡——人后来手动取消归档，平台不会自作主张
#: 再归档一次。
ARCHIVE_DEFERRED = "子话题还挂着未决的验收卡，归档推迟到验收卡有结果之后"

#: 待办消解后的落款。只要不再等于 ARCHIVE_DEFERRED，这张卡就退出扫描。
ARCHIVE_DEFERRED_DONE = "验收卡已决议，推迟的归档已经落下"

#: 验收卡决议后，留给子话题重新递卡的宽限。驳回/作废的语义是"回去改了再来"，
#: 而 `AcceptService.create_card` 只受理 active 话题——归档落得太快，改完就递
#: 不出来了。宽限内递出新卡就重新受保护；宽限过完还没有新卡，归档才落下，这是
#: "不留僵尸子话题"的兜底。
ARCHIVE_REFILE_GRACE_MINUTES = 30


class ConclusionCard(UuidPk, Timestamps, Base):
    __tablename__ = "conclusion_cards"
    __table_args__ = (
        # The two hot lookups: "this sub-topic's live card" and "cards this
        # parent still owes a verdict on".
        Index("ix_conclusion_cards_topic_status", "topic_id", "status"),
        Index("ix_conclusion_cards_receiver_status", "receiver_topic_id", "status"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # The room this conclusion happened in — the same room on both ends now
    # that work is a thread rather than a room of its own.
    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True
    )
    # The thread that produced the conclusion (分身). NULL would mean the room
    # concluded to itself, which is not a thing — but the column stays nullable
    # so historical cards filed before work was a thread still load.
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # 收方是话题不是人 —— the room (本体) that settles this card.
    receiver_topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True
    )
    # 阶段一：conclude 的原文，原样包进来。阶段二会在旁边加结构化栏位
    # (结论/证据/不确定性/适用边界/下一步建议)，这一列保持不变。
    conclusion: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[ConclusionStatus] = mapped_column(
        Enum(ConclusionStatus, native_enum=False, length=16),
        default=ConclusionStatus.open,
    )
    # Who settled it: a handle, or "system" for 默认采信 (turn end / timeout).
    settled_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # need-evidence / escalate 的理由；采信不需要理由（零成本是它的重点）。
    settle_reason: Mapped[str] = mapped_column(Text, default="", server_default="")
    # 阶段二：打回时引用的那条 blocking 不确定性。阶段一恒为 NULL（卡面还没有
    # 结构化栏位可引用），校验代码已就位，见 services._check_evidence_anchor。
    blocking_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    returned_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # 绝对超时线：过了它还 open，扫描器直接采信。
    digest_deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
