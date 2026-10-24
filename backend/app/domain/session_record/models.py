"""One run of a session's raw records.

A session's runner numbers every record its harness writes (Claude Code's
stream-json lines, Codex's app-server notifications, pi's entries) and the
backend mirrors that journal (`agent.harness.driven`). This table keeps those
records the way they came, so that what the agent did can be read after the
mirror has forgotten it. A sealed run's object is kept in the bucket for good;
its row for `retention.RETENTION` (30 days, `retention.expire`). A permanent
bucket index keeps sealed runs readable after their database rows expire.

A row is a run of consecutive records of one session's journal, all of one
turn. A record arrives as a run of one, with the record in the row
(`records`); once it is a few minutes old, the sealer joins a turn's run of
them into one gzip object in the private bucket and replaces those rows with a
single row that names the object (`object_key`). The records themselves never
change: the table refuses UPDATE (the trigger in the migration), and sealing is
a DELETE of the short runs and an INSERT of the long one, in one transaction.

A record is named by where its journal put it: (`first_sequence`,
`first_at`) of its run is the runner's sequence and the time the runner wrote
it. A new runner journal numbers from 1 again, at a later time, so the pair
still names one record, and reading the same page twice lands it once.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import UuidPk


class SessionRecord(UuidPk, Base):
    __tablename__ = "session_records"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "agent_handle",
            "harness",
            "first_sequence",
            "first_at",
            name="uq_session_records_conversation_id",
        ),
        CheckConstraint(
            "(records IS NULL) <> (object_key IS NULL)",
            name="ck_session_records_one_home",
        ),
        # A conversation's records in the order they were written.
        Index("ix_session_records_conversation_id", "conversation_id", "first_at"),
        Index("ix_session_records_turn_id", "turn_id", "first_at"),
        # What the sealer picks up: runs still held in the row.
        Index(
            "ix_session_records_unsealed",
            "last_at",
            postgresql_where="object_key IS NULL",
        ),
        # What the retention sweep picks up: sealed runs.
        Index(
            "ix_session_records_sealed",
            "last_at",
            postgresql_where="object_key IS NOT NULL",
        ),
    )

    # The conversation (`conversations.id`), without a foreign key:
    # `retention.forget_gone` deletes the rows of a conversation that is gone
    # and of a channel a person deleted (whose row stays), in one place. Their
    # objects stay in the bucket either way (transcripts are kept permanently, #4398).
    conversation_id: Mapped[uuid.UUID] = mapped_column()
    # The turn the runner stamped on these records (`cheese.work_id`); empty for
    # what a session wrote before its first input.
    turn_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    agent_handle: Mapped[str] = mapped_column(String(128))
    harness: Mapped[str] = mapped_column(String(32))
    first_sequence: Mapped[int] = mapped_column(BigInteger)
    last_sequence: Mapped[int] = mapped_column(BigInteger)
    first_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    count: Mapped[int] = mapped_column(Integer)
    # The size of the run's records as stored (scrubbed JSON, before gzip).
    bytes: Mapped[int] = mapped_column(BigInteger)
    # Each entry is {"sequence", "at", "record"}; NULL once sealed.
    records: Mapped[list | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    # Where the sealed run lives in the private bucket: gzip JSONL, one entry a line.
    object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SiteEventArchive(UuidPk, Base):
    """One batch of the retired 现场 rows `retired_site` moved out of `blocks`:
    the object in the private bucket that holds them, and how many rows it took.
    A row is written in the transaction that deletes those rows, so the table
    lists exactly what has left the database and where it went."""

    __tablename__ = "site_event_archives"

    # The conversation the rows were in, without a foreign key: the archive
    # outlives the conversation, as its object does.
    conversation_id: Mapped[uuid.UUID] = mapped_column()
    object_key: Mapped[str] = mapped_column(String(512))
    rows: Mapped[int] = mapped_column(Integer)
    # The object's size: gzip JSONL, one row a line.
    bytes: Mapped[int] = mapped_column(BigInteger)
    archived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
