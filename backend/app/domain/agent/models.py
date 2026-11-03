"""一轮 = 一次投喂到它停下来之间的那段区间。

A row here is one prompt handed to a session and what became of it. It is not a
unit of work — the work is the task, which outlives any number of these — and it
is not a container the platform schedules against. It is an interval with three
moments and nothing else: ``started_at`` when the platform decided to speak,
``delivered_at`` when the transport accepted the write, ``stopped_at`` when the
session came back, and ``stop_reason`` — which closer did it, since the moment
alone cannot say. Running is exactly ``stopped_at IS NULL``.

This used to be a JSON file at ``{workspace_root}/.turns-inflight.json``, whose
whole job was to survive the process. It did that, and paid for it: it was a
host-local file, so it could not be read next to the topic it describes, could
not be joined against the blocks that carry the same ``turn_id``, and was
invisible to any other backend process. Worse, it was a registry of the LIVING
— an entry existed only while a turn ran and was deleted at the end — so the
question "did this prompt ever reach the session" was answerable for thirty
seconds and then gone forever, which is why the orphan sweep reconstructed it
from forensics (does any block carry this turn id) instead of reading it.

Rows are closed, never deleted. A turn id lives on in every block it produced,
and an interval that ends by being erased is one nobody can ask about afterwards.

No ``created_at``/``updated_at``: the row IS its timestamps, and a creation time
that always equals ``started_at`` is a second answer to one question.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk

# The registry `conversation_id` points at: mapped wherever this is, so the
# foreign key resolves in a process that never imports `app.models`.
from app.domain.conversation.models import Conversation  # noqa: F401

#: Who ended a turn's interval, as that closer names itself in
#: ``AgentTurn.stop_reason``. One word each: after an incident the question is
#: 「是谁让它停的」, and before this column the only answer was 「它停了」.
STOP_FINISHED = "finished"
STOP_STOPPED = "stopped"
STOP_SWEEP = "sweep"
STOP_TASK_CLOSED = "task_closed"
STOP_TAKEN_OVER = "taken_over"
STOP_UNDELIVERED = "undelivered"
#: The platform destroyed the machine this turn was running on: its executor went
#: with it, and no report about that turn can ever arrive
#: (``turn_inputs.close_turns_of_a_destroyed_machine``).
STOP_MACHINE_DESTROYED = "machine_destroyed"


class AgentTurn(Base):
    __tablename__ = "agent_turns"
    __table_args__ = (
        # The sweep's only question: which intervals are still open? Partial, so
        # the index stays the size of what is running rather than of every turn
        # this platform has ever run.
        Index(
            "ix_agent_turns_open",
            "started_at",
            postgresql_where=("stopped_at IS NULL"),
        ),
    )

    # The runtime's turn id, not one minted here — blocks already carry it.
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    # The conversation this turn ran in: a room or a task.
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    # Which attempt-chain this turn belongs to. A resumed turn keeps the id of
    # the first attempt, so every key its predecessor claimed still matches and
    # its side effects are not repeated.
    continuation_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    # Who spoke and what they said. When nothing is pending in the topic — every
    # platform-authored turn, and any human message whose block a restart beat —
    # this is the only copy of it.
    author: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text, default="")
    is_resume: Mapped[bool] = mapped_column(Boolean, default=False)
    # May this turn be re-delivered by re-submitting `content`?
    #
    # Yes for anything whose content IS the task: a person's message, a task's
    # opening prompt, and every platform nudge (验收卡被驳回、上游合并冲突、CI
    # 红了、后台任务跑完了). Each is a standalone instruction, and re-sending it
    # verbatim is the whole of what "the work still happens" means.
    #
    # No for a resume nudge. 「从上一轮的断点继续」 says nothing to a session that
    # never heard the task, and re-issuing it is exactly what stacked five zombie
    # turns on one topic in a day (#324).
    resendable: Mapped[bool] = mapped_column(Boolean, default=False)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # The transport accepted the write. Its absence is the platform's own
    # statement that the session never heard this prompt — the one condition
    # under which re-sending it is safe rather than a second copy of a task
    # somebody is already working on.
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    stopped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    # 这一轮是怎么结束的，由关掉它的那一段代码写下（`STOP_REASONS`）。
    # 没有这一列的时候，只有「停了多久」没有「谁让它停的」：2026-10-22 的一轮
    # 开了 13 小时，事后只能查到它停了，查不到是会话自己收的、还是清扫收的、
    # 还是别的什么收的。NULL = 这一列加上之前就结束的那些行，不是「不知道」。
    stop_reason: Mapped[str | None] = mapped_column(String(32), default=None)
    # Admission refused a `/v1/messages` call for this turn's place because the
    # project's compute credits are spent (#715). First-writer-wins, like
    # `delivered_at`: the proxy caches a verdict for 30s and Claude Code retries
    # ten times, so admission is asked again and again for the SAME refusal —
    # this is what lets the room notice fire once instead of once per retry.
    credits_refused_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    # What ending this turn needs and nothing else records: where its model
    # traffic went (which decides how its spend is read back) and the message it
    # answers. Written when the turn is assembled, so a backend that picks the
    # turn up after the one that started it is gone can still close its books.
    # NULL on a turn nobody assembled — one a session started by itself.
    route: Mapped[str | None] = mapped_column(String(32), default=None)
    reply_to: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)
    # Whose conversation this turn ran in: the agent's handle in its project,
    # …and the conversation itself, stamped from the session's own info
    # (FB-56 legacy③; NULL on rows older than the column).
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # the same key its session is kept under. A room seats several teammates
    # side by side, so the room alone does not say whether this turn's session
    # is the one still answering. NULL until the turn is assembled — before
    # then no agent has been chosen.
    agent_handle: Mapped[str | None] = mapped_column(String(64), default=None)


class GatewayAdminAudit(UuidPk, Timestamps, Base):
    """后台对网关做过的每一次写动作 —— 成功的和失败的都留。

    这个后台能动的是别人真金白银在用的路由：删一个模型、停一个模型、给一个项目
    改预算，哪一件错了都不是重启能挽回的，而网关那边只留最后一次的现状，不记谁在
    什么时候把它改成这样。所以「谁在什么时候对哪个对象做了什么」只能由平台自己记。

    **失败也落一行**，而且必须落 —— 页面上的「最近操作」要能回答「我点了停用，为
    什么没生效」，一条失败的记录（外加网关给的那句原因）就是答案；只记成功的话，
    失败在界面上和在库里都等同于「什么都没发生」，人只会反复点。

    ``before``/``after`` 存操作前后的快照（JSON），是为了回答「原来是怎样的」——
    没有它，一条 `model.update` 只能证明有人动过，证明不了动了什么。两个字段都
    **绝不包含任何上游凭据**：``api_key`` 由服务层在写入前一律剔除，它属于网关，
    进审计表就等于把一把真实密钥复制进了另一处可读的地方。

    ``created_at`` 上那条**倒序**索引服务这一个读法：后台按时间倒序取最近 N 条。
    Postgres 的 btree 两个方向都能扫，但把这个唯一的用法写进索引，读的人不必再推。
    """

    __tablename__ = "gateway_admin_audit"
    __table_args__ = (
        Index("ix_gateway_admin_audit_created_at", text("created_at DESC")),
    )

    # 操作者。后台每个 handler 都带 `PlatformAdminDep`，这个 handle 直接来自它。
    actor_handle: Mapped[str] = mapped_column(String(64))
    # 动作对象：模型名、或项目 uuid 的字符串形式。
    target: Mapped[str] = mapped_column(String(128))
    # 固定的一组动作名，页面按它分组显示（见 `gateway_models` 里的调用点）。
    action: Mapped[str] = mapped_column(String(32))
    # "ok" / "failed"。字符串而不是布尔，是为了以后能加「已回滚」这类第三态。
    result: Mapped[str] = mapped_column(String(16))
    # 失败原因（网关或不变式给的中文原话）；成功时为 None。
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class PlatformModelRole(Timestamps, Base):
    """Which gateway model a platform model role runs on, as an administrator
    pointed it (``app.core.model_roles`` lists the roles and reads them).

    One row per role that has been pointed; a role with no row reads its
    ``Settings`` field, so a deployment that never touches the page behaves as
    it did before the table existed. Who pointed it and when is in
    ``gateway_admin_audit`` (action ``role.set``), next to the model edits a
    role is checked against.
    """

    __tablename__ = "platform_model_roles"

    role: Mapped[str] = mapped_column(String(64), primary_key=True)
    model: Mapped[str] = mapped_column(String(200))


class TurnResume(UuidPk, Base):
    """A resume the platform owes one seat after its turn failed on a transient
    model-service error (`agent.turn_resume`).

    It is not the agent's own alarm (`delivery.models.TimedDelivery`, set by a
    participant) and not a delivery (`deliveries`, an event that already
    happened): it is the platform's decision to start this seat again later,
    the reason it decided so, and how many times in a row it already has.
    When it falls due it either becomes one instruction on the delivery ledger
    (``handed_at``) or is dropped with the reason it no longer applies
    (``dropped_at``/``dropped_reason``): a person spoke, the seat already ran
    again, the conversation closed, the streak used up its attempts. Rows are
    kept: they record why an agent started by itself, and the streak count
    reads them back.
    """

    __tablename__ = "turn_resumes"
    __table_args__ = (
        # One owed resume per seat and conversation, whichever process or
        # failure asks: a second failure while one waits moves that row.
        Index(
            "uq_turn_resumes_conversation_id_recipient_handle",
            "conversation_id",
            "recipient_handle",
            unique=True,
            postgresql_where=text("handed_at IS NULL AND dropped_at IS NULL"),
        ),
        Index(
            "ix_turn_resumes_due_at",
            "due_at",
            postgresql_where=text("handed_at IS NULL AND dropped_at IS NULL"),
        ),
        Index(
            "ix_turn_resumes_conversation_id_recipient_handle",
            "conversation_id",
            "recipient_handle",
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    # The room or task the failed turn ran in; the resume runs there too.
    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    # The seat, as the delivery ledger names it (`agent_instance_handle`).
    recipient_handle: Mapped[str] = mapped_column(String(64))
    agent_instance_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    failed_turn_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    # A `resumable_failures` kind: rate_limited, overloaded, stream_ended, …
    reason: Mapped[str] = mapped_column(String(32))
    # The service's own words, credentials masked, so the row says why.
    said: Mapped[str] = mapped_column(Text, default="")
    # 1 for the first resume after a failure, n for the n-th in a row.
    attempt: Mapped[int] = mapped_column(Integer)
    failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    handed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dropped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dropped_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)


class SessionState(Base):
    """One seat's session as its runner declares it (`harness/driven/lifecycle.py`).

    A seat is (conversation, agent, harness): the key ``agent_sessions`` is
    written under. ``worker_epoch`` is the registration the platform issued its
    runner last; a runner, or a platform process, holding an older one can no
    longer change anything here (``session_state.take``). ``worker_status`` is
    what the runner said the session is doing, ``idle`` or ``running``, and
    ``changed_at`` when it last said something different. ``run_epoch`` /
    ``run_seq`` name the run the current process last began: the
    ``(epoch, seq)`` of its ``running`` event, which the runner names the run
    by and a task's report carries (`session_state.run_started`). Whether the
    session is reachable is not here: that is a lease the reading process
    holds, and unreachable is not a status the session has.
    """

    __tablename__ = "session_states"
    __table_args__ = (
        CheckConstraint(
            "worker_status IN ('idle', 'running')",
            name="ck_session_states_worker_status",
        ),
        Index(
            "ix_session_states_running",
            "conversation_id",
            postgresql_where=text("worker_status = 'running'"),
        ),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    agent_handle: Mapped[str] = mapped_column(String(64), primary_key=True)
    harness: Mapped[str] = mapped_column(String(32), primary_key=True)
    worker_epoch: Mapped[int] = mapped_column(BigInteger)
    worker_status: Mapped[str] = mapped_column(String(16))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    run_epoch: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    run_seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: When the runner last reported the session's process gone.
    exited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SessionOffset(Base):
    """How far the platform has applied what one registration of a seat's
    runner reported: the consumer offset, committed in the same transaction as
    the effects of the events it covers (``session_state.take``).

    Events of one runner arrive in sequence order and are applied in it, so an
    event at or below ``applied_seq`` for its ``(seat, epoch)`` has been
    applied, and applying it again is a no-op: the unique key of an event is
    ``(seat, epoch, seq)``, and this row is the high-water mark of it. An event
    is acknowledged to the runner only once ``projected_seq`` covers it too.
    """

    __tablename__ = "session_offsets"

    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    agent_handle: Mapped[str] = mapped_column(String(64), primary_key=True)
    harness: Mapped[str] = mapped_column(String(32), primary_key=True)
    epoch: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    applied_seq: Mapped[int] = mapped_column(BigInteger)
    #: How far what follows from those events outside this transaction (the
    #: room's books, what is told) has been done: an event above it and at or
    #: below ``applied_seq`` was applied, and what follows from it is done again
    #: when it is delivered again (``session_state.projected``).
    projected_seq: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0"
    )
    #: How far the room has landed the closing results those events carried:
    #: done again, the rest of an event's projection does not hand the room
    #: the same result twice (`session_state.result_landed`).
    landed_seq: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
