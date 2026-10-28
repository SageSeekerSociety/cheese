"""A person's conversations with their 芝士 outside any project (#2285).

There is one 芝士 per person and many conversations; each belongs to the place
it was started, named by ``place_kind`` and ``place_id`` — a task today, other
places later. A conversation is visible to its owner only.

What was said is kept as ``AssistantMessage`` rows, in order; the panel shows
them. The model's own copy of the conversation is its session's, on the session
host (``agent.personal.session``).
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class AssistantConversation(UuidPk, Timestamps, Base):
    __tablename__ = "assistant_conversations"
    __table_args__ = (
        # "This person's conversations here, latest first": the panel's list and
        # the conversation a place opens on.
        Index(
            "ix_assistant_conversations_owner_place",
            "user_id",
            "place_kind",
            "place_id",
            "last_active_at",
        ),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    place_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    place_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # The first question, cut short; empty until one is asked.
    title: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AssistantGatewayKey(Timestamps, Base):
    """A person's own virtual key on the gateway, and how far its spend has been
    charged to them.

    Every model call a person's 芝士 makes is made on this key, so what the
    gateway records under it is exactly what that person asked for, priced the
    way the gateway prices it (cache reads at the cache rate). ``usage_ckpt`` is
    the cumulative spend already charged, in the shape a project's checkpoint
    has (``agent.gateway.drain_new_usage``)."""

    __tablename__ = "assistant_gateway_keys"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(Text, nullable=False)
    # The one model the key may call (``keys.person_key``).
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    usage_ckpt: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class AssistantMessage(UuidPk, Timestamps, Base):
    __tablename__ = "assistant_messages"
    __table_args__ = (
        Index("ix_assistant_messages_conversation_seq", "conversation_id", "seq"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assistant_conversations.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    #: ``user``, ``assistant``, or ``platform``: what the platform said to
    #: start a routine's run (``personal.routines``). Only ``user`` is the
    #: person's own words; the gate reads nothing else.
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: The answer was stopped by the person before it was done; ``text`` is
    #: what had been written.
    stopped: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    #: What 芝士 did while answering, besides what it said: the tools it called
    #: (``steps``), what it wrote into drafts (``edits``) and what it published
    #: (``published``), as the question's stream told them. NULL for a question
    #: asked before it was kept, and for the person's own messages.
    parts: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class AssistantChange(Timestamps, Base):
    """One write a 芝士 made while answering a question: what it changed, kept
    so the person who asked can see it and undo it (``assistant.changes``).

    ``work`` is the answer it was made in (the delegated credential's): the
    writes of one answer are listed by it. ``kind`` names what was written —
    ``draft`` (a 题目 draft, ``target`` its id) or ``doc`` (a living document,
    ``target`` its id) — and ``payload`` is what that kind says it changed.
    ``undone`` lists the parts of it the person undid, in the kind's own keys.
    """

    __tablename__ = "assistant_changes"
    __table_args__ = (
        Index("ix_assistant_changes_work", "work", "id"),
        # The person's own, and gone with their account.
        Index("ix_assistant_changes_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey(
            "user.id", ondelete="CASCADE", name="fk_assistant_changes_user_id_user"
        ),
        nullable=False,
    )
    work: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    target: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    undone: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )


class AssistantProposal(Timestamps, Base):
    """Something a 芝士 drafted for the person to send, which nobody else sees
    until they do (``assistant.proposals``): a message to a channel or a task,
    or a task to open in a channel with what it is for.

    ``work`` is the answer it was drafted in. ``state`` is ``pending`` until the
    person sends it (``sent``, with what was made in ``result``) or puts it
    aside (``dismissed``). Sending is the person's own act, through the routes
    they would use by hand; 芝士 cannot send.
    """

    __tablename__ = "assistant_proposals"
    __table_args__ = (
        Index("ix_assistant_proposals_work", "work", "id"),
        # The person's own, and gone with their account.
        Index("ix_assistant_proposals_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey(
            "user.id", ondelete="CASCADE", name="fk_assistant_proposals_user_id_user"
        ),
        nullable=False,
    )
    work: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class AssistantRules(Timestamps, Base):
    """What a person tells their 芝士 about acting for them
    (``assistant.gate``): which kinds of action it always asks first
    (``ask``, codes of ``gate.CATEGORIES``), in their own words what it may
    simply do (``allow_text``) and what it should ask about (``ask_text``),
    and whether it keeps a memory of them (``memory``).
    Only the person sets these, on their settings page; 芝士 cannot.
    """

    __tablename__ = "assistant_rules"

    user_id: Mapped[int] = mapped_column(
        ForeignKey(
            "user.id", ondelete="CASCADE", name="fk_assistant_rules_user_id_user"
        ),
        primary_key=True,
    )
    ask: Mapped[list] = mapped_column(JSONB, nullable=False)
    allow_text: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    ask_text: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    #: Whether their 芝士 uses and keeps a memory of them
    #: (``assistant.memory``); on until they turn it off.
    memory: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    #: When that memory was last tidied (``assistant.memory_tidy``); the next
    #: tidy waits from it, and taking one moves it.
    memory_tidied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AssistantGateCheck(Base):
    """One action a 芝士 was about to take for a person, and what the gate made
    of it (``assistant.gate``): done (``allowed``), turned into a card for
    the person (``asked``) or handed back to 芝士 (``returned``), at which ``stage`` that was settled — by one of
    the person's rules, by the gate's quick look, or by its second, reasoned
    look — why, and what the look cost. The platform pays for the looks; these
    rows are how it sees them, and how the person sees what was done for them.
    """

    __tablename__ = "assistant_gate_checks"
    __table_args__ = (
        Index("ix_assistant_gate_checks_user_id", "user_id", "id"),
        Index("ix_assistant_gate_checks_created_at", "created_at"),
        Index("ix_assistant_gate_checks_conversation_id", "conversation_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey(
            "user.id", ondelete="CASCADE", name="fk_assistant_gate_checks_user_id_user"
        ),
        nullable=False,
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "assistant_conversations.id",
            ondelete="SET NULL",
            name="fk_assistant_gate_checks_conversation_id",
        ),
        nullable=True,
    )
    work: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    #: What the action was, as the gate and the person read it.
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(nullable=False, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AssistantRoutine(UuidPk, Timestamps, Base):
    """Something a person's 芝士 does on its own, on a clock or when something
    happens to the person (``personal.routines``): a rule, made by 芝士 in a
    conversation when the person asked for it.

    ``words`` is what the person said in that conversation: the only words a
    run's gate reads as theirs. ``title`` and ``note`` are 芝士's own, and
    say nothing for the person. ``trigger`` is ``schedule`` (``spec`` is a
    ``routine.schedule`` spec, or ``{"freq": "once", "at": ISO}``) or one of
    ``personal.EVENTS`` (``spec`` says what it is limited to, and ``once``
    when it fires only on the first). A rule that fires once runs in the
    conversation it was made in.
    ``paused`` says why a paused rule stopped: ``person``, ``credits`` (out of
    credits) or ``away`` (not signed in for long). ``state`` is ``active``,
    ``paused``, or ``done``: a one-off that has run, cleared with its runs
    once the cards they left expire.
    """

    __tablename__ = "assistant_routines"
    __table_args__ = (
        Index("ix_assistant_routines_user_id", "user_id"),
        Index(
            "ix_assistant_routines_due",
            "next_run_at",
            postgresql_where=text("state = 'active' AND next_run_at IS NOT NULL"),
        ),
        Index("ix_assistant_routines_conversation_id", "conversation_id"),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey(
            "user.id", ondelete="CASCADE", name="fk_assistant_routines_user_id_user"
        ),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    words: Mapped[str] = mapped_column(Text, nullable=False, default="")
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    spec: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )
    paused: Mapped[str] = mapped_column(
        String(16), nullable=False, default="", server_default=""
    )
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: The conversation it was made in.
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "assistant_conversations.id",
            ondelete="SET NULL",
            name="fk_assistant_routines_conversation_id",
        ),
        nullable=True,
    )


class AssistantRun(UuidPk, Base):
    """One time a person's 芝士 went to work on its own: a routine fired.

    ``occurrence`` names what started it — a scheduled moment, a notification
    — once per person, so a second sweep finds the row instead of running the
    same thing twice. Its id is the answer it ran as (``work``): the cards it
    put up are found by it."""

    __tablename__ = "assistant_runs"
    __table_args__ = (
        UniqueConstraint("user_id", "occurrence", name="uq_assistant_runs_occurrence"),
        Index("ix_assistant_runs_routine_id", "routine_id", "created_at"),
        Index(
            "ix_assistant_runs_open",
            "user_id",
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        Index("ix_assistant_runs_conversation_id", "conversation_id"),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey(
            "user.id", ondelete="CASCADE", name="fk_assistant_runs_user_id_user"
        ),
        nullable=False,
    )
    routine_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "assistant_routines.id",
            ondelete="CASCADE",
            name="fk_assistant_runs_routine_id",
        ),
        nullable=False,
    )
    occurrence: Mapped[str] = mapped_column(String(200), nullable=False)
    #: What started it, as the run is told it.
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: What started it, as the conversation shows it (``{"task", "event"}``
    #: for a task's review), when that is more than the rule's name.
    shown: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "assistant_conversations.id",
            ondelete="SET NULL",
            name="fk_assistant_runs_conversation_id",
        ),
        nullable=True,
    )
    #: ``queued``, ``running``, ``done``, ``failed`` or ``skipped``.
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    cost_usd: Mapped[float] = mapped_column(nullable=False, default=0.0)
    #: How many actions it left for the person to confirm.
    asked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: 芝士 sent the person a notification itself (``cheese_notify``), so
    #: its end is not announced again.
    told: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
