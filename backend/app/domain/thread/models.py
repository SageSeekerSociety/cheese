"""聊天底座 — `domain/thread` (万物皆块 §2.2).

A **thread** is a chat group. It is independent of any project: `project_id` is
nullable, so a standalone 微信/飞书-style group has `project_id = NULL`; a thread may
optionally be attached to a project later. Membership (`thread_membership`) holds both
humans and agents — the substrate never distinguishes; an agent is just a `user_id`.
Pulling a member in that needs consent (an agent, whose owner must approve) goes through
`thread_membership_application`, modelled on `domain/team`'s approval workflow.

Messages themselves are `block` rows (see `domain/block`); a thread only owns identity,
title and membership.
"""

from datetime import datetime
from enum import Enum, IntEnum

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Sequence,
    SmallInteger,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

thread_seq = Sequence("thread_seq")
thread_membership_seq = Sequence("thread_membership_seq")
thread_application_seq = Sequence("thread_application_seq")


class ThreadKind(IntEnum):
    GENERAL = 0  # an ordinary group chat
    DIRECT = 1  # a 1:1 conversation (private chat) — title derived from the peer
    MANAGEMENT = 2  # a management group (§4); reserved


class ThreadMemberRole(IntEnum):
    MEMBER = 0
    ADMIN = 1
    OWNER = 2


class Thread(Base):
    __tablename__ = "thread"

    id: Mapped[int] = mapped_column(
        BigInteger, thread_seq, primary_key=True, server_default=thread_seq.next_value()
    )
    # Nullable on purpose: chat is project-independent. A thread MAY hang off a project.
    project_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    kind: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=ThreadKind.GENERAL)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ThreadMembership(Base):
    __tablename__ = "thread_membership"
    __table_args__ = (Index("ix_thread_membership_thread_user", "thread_id", "user_id"),)

    id: Mapped[int] = mapped_column(
        BigInteger,
        thread_membership_seq,
        primary_key=True,
        server_default=thread_membership_seq.next_value(),
    )
    thread_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("thread.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    role: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=ThreadMemberRole.MEMBER)
    # nullable; per-group attention override (§4). Delivery = override ?? agent_default.
    attention_policy_override: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Read-receipt high-water mark: the id of the newest block this member has read. For a
    # human this advances when they view the message in the web UI; for an agent it advances
    # when the message is actually delivered into its cheeselet. Null = has read nothing yet.
    last_read_block_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApplicationType(str, Enum):
    REQUEST = "REQUEST"  # a user asks to join a thread; a thread admin approves
    INVITATION = "INVITATION"  # a thread admin invites a user; the user (or an agent's owner) approves


class ApplicationStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    CANCELED = "CANCELED"


class ThreadMembershipApplication(Base):
    """A pending add/join that needs consent.

    Adding an **agent** to a thread mints an INVITATION whose approver is the agent's
    owner (the human who enrolled its device). Inviting a **human** mints an INVITATION
    the human answers. A human asking to join mints a REQUEST a thread admin answers.
    """

    __tablename__ = "thread_membership_application"

    id: Mapped[int] = mapped_column(
        BigInteger,
        thread_application_seq,
        primary_key=True,
        server_default=thread_application_seq.next_value(),
    )
    thread_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("thread.id"), nullable=False)
    # The user who would become a member (the invitee / the requester).
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    initiator_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Who must consent. For an agent invite this is the agent's owner; for a human invite
    # it is the invited human; for a join request it is left null (any thread admin acts).
    approver_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)

    type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    role: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=ThreadMemberRole.MEMBER)

    message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    processed_by_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
