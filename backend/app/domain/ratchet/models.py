"""The ratchet snapshot table: one row per collected CI run."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import UuidPk


class RatchetSnapshot(UuidPk, Base):
    """One `ratchet-snapshot` artifact, as collected.

    The columns are the ones the page filters and orders by; the snapshot
    itself is kept verbatim in ``payload``. Both, on purpose: a column per field
    the page needs means the page does not have to know the collector's shape,
    and keeping the whole document means a question the page did not ask yet is
    still answerable from the archive (and that a stored row can be compared
    with what the checker actually said).

    ``collection`` is the snapshot's own word, and it is not a verdict on the
    tree: ``ok`` means the collector ran, ``failed`` means it could not (the CI
    job is red and ``reason`` says why), ``unreadable`` means the artifact
    could not be understood by this code at all — an older format, or a zip
    that is not a snapshot. The three are kept apart because a hole in the
    series and a red snapshot are different facts, and neither is a pass.

    The row exists for runs that produced no snapshot on purpose: a red
    collection is a point in the series, and dropping it would make the line
    jump from the last good commit to the next one as if the checks had agreed.
    """

    __tablename__ = "ratchet_snapshots"

    # "owner/repo". Present so the table can hold more than the platform's own
    # repository without a shape change; the ratchet page reads the configured one.
    repo: Mapped[str] = mapped_column(String(200))
    # The CI run this came from. The identity of a point: the same commit can be
    # collected twice (a push, then the weekly schedule), and a re-poll of the
    # same run must not append a second row.
    workflow_run_id: Mapped[int] = mapped_column(BigInteger)
    commit_sha: Mapped[str] = mapped_column(String(64))
    commit_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    collected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The snapshot format this row was read as; a later reader can tell which
    # rows predate a shape change without parsing every payload.
    snapshot_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # ok | failed | unreadable — see the class docstring.
    collection: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The collector's document, verbatim, or NULL when there was none to keep.
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # What the CI run said about the artifact itself, for 「谁采的、在哪看的」.
    run_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    artifact_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("repo", "workflow_run_id", name="uq_ratchet_snapshots_run"),
        Index("ix_ratchet_snapshots_repo_collected", "repo", "collected_at"),
        Index("ix_ratchet_snapshots_repo_commit", "repo", "commit_sha"),
    )
