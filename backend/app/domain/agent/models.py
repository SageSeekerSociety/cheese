"""一轮 = 一次投喂到它停下来之间的那段区间。

A row here is one prompt handed to a session and what became of it. It is not a
unit of work — the work is the task, which outlives any number of these — and it
is not a container the platform schedules against. It is an interval with three
moments and nothing else: ``started_at`` when the platform decided to speak,
``delivered_at`` when the transport accepted the write, ``stopped_at`` when the
session came back. Running is exactly ``stopped_at IS NULL``.

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
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


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
    # The room this turn ran in — always a room, never a piece of work.
    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True
    )
    # Which thread in it, NULL when the turn ran on the room's own main line.
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True
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
    # Yes for anything whose content IS the task: a person's message, a 分身's
    # kickoff prompt, and every platform nudge (验收卡被驳回、上游合并冲突、CI
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
    # Admission refused a `/v1/messages` call for this turn's place because the
    # project's compute credits are spent (#715). First-writer-wins, like
    # `delivered_at`: the proxy caches a verdict for 30s and Claude Code retries
    # ten times, so admission is asked again and again for the SAME refusal —
    # this is what lets the room notice fire once instead of once per retry.
    credits_refused_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )


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
