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
from forensics (does any block carry this turn id, is there anything in the
spool) instead of reading it.

Rows are closed, never deleted. A turn id lives on in every block it produced,
and an interval that ends by being erased is one nobody can ask about afterwards.

No ``created_at``/``updated_at``: the row IS its timestamps, and a creation time
that always equals ``started_at`` is a second answer to one question.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


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
