"""Thread: the 2.0 conversation container (群聊 / 话题).

A thread is a session = (future) git branch, nestable into a tree via
``parent_thread_id``. It is anchored to a ``project_id`` (the aggregate
root) and is namespace-isolated from the legacy tag-style ``domain/topics``.

Membership is many-to-many over users AND agents. Each membership carries the
member's role and their **attention policy** — the settled schema the
orchestrator consults to decide when to wake an agent (the runtime is built
later). ``cursor_block_id`` is the attention watermark: on wake, the member is
delivered blocks after it.
"""

from datetime import datetime
from enum import IntEnum

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    Sequence,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

thread_seq = Sequence("thread_seq")
thread_membership_seq = Sequence("thread_membership_seq")


class ThreadKind(IntEnum):
    GENERAL = 0
    MANAGEMENT = 1  # encodes the management tree: one superior + subordinates


class MemberKind(IntEnum):
    USER = 0
    AGENT = 1


class MemberRole(IntEnum):
    MEMBER = 0
    SUPERIOR = 1  # in a management thread, listens broadly
    SUBORDINATE = 2  # in a management thread, acts on mention


class AttentionPolicy(IntEnum):
    """When the orchestrator wakes this member. Delivery is uniform: blocks
    after ``cursor_block_id``. Only user speech triggers the loop-safe modes;
    ALL_MESSAGES (incl. agent messages) is reserved for management superiors,
    kept acyclic by the superior/subordinate attention asymmetry."""

    MENTION_ONLY = 0
    IDLE_WINDOW = 1  # woken when idle > window AND unread user speech exists
    ALL_USER_MESSAGES = 2
    ALL_MESSAGES = 3


class Thread(Base):
    __tablename__ = "thread"

    id: Mapped[int] = mapped_column(
        BigInteger, thread_seq, primary_key=True, server_default=thread_seq.next_value()
    )
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    parent_thread_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("thread.id"), nullable=True, index=True
    )
    kind: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=ThreadKind.GENERAL.value
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_by_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ThreadMembership(Base):
    __tablename__ = "thread_membership"
    __table_args__ = (
        UniqueConstraint(
            "thread_id", "member_id", "member_kind", name="uq_thread_member"
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        thread_membership_seq,
        primary_key=True,
        server_default=thread_membership_seq.next_value(),
    )
    thread_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("thread.id"), nullable=False, index=True
    )
    member_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    member_kind: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=MemberKind.USER.value
    )
    role: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=MemberRole.MEMBER.value
    )
    attention_policy: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=AttentionPolicy.MENTION_ONLY.value
    )
    attention_window_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cursor_block_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
