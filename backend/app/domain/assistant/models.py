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
    false,
    func,
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
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user | assistant
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
    (``ask``, codes of ``gate.CATEGORIES``), and in their own words what it may
    simply do (``allow_text``) and what it should ask about (``ask_text``).
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


class AssistantGateCheck(Base):
    """One action a 芝士 was about to take for a person, and what the gate made
    of it (``assistant.gate``): done (``allowed``) or turned into a card for
    the person (``asked``), at which ``stage`` that was settled — by one of
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
