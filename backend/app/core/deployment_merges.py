"""What merging another deployment's export into this one wrote, batch by batch.

``scripts/merge_deployment.py`` brings rows from another Cheese deployment into
this database. Every write it makes is recorded here, under the batch it
belongs to, so that one batch can be taken back exactly and a rerun of a batch
that is already in finds nothing to do.

- ``deployment_merge_batches``: one row per batch, and whether it is in.
- ``deployment_merge_rows``: one row per database row the batch inserted or
  updated, in the order it wrote them. ``previous`` holds the columns an update
  changed, as they were; it is null for an insert. ``written`` is the row as the
  batch left it, so a revert can tell whether anyone changed it since.
- ``deployment_merge_files``: one row per file the batch created, in the uploads
  tree or in a blob store.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class DeploymentMergeBatch(Base):
    __tablename__ = "deployment_merge_batches"

    batch: Mapped[str] = mapped_column(String(100), primary_key=True)
    #: ``imported`` while its writes are in, ``reverted`` once taken back.
    state: Mapped[str] = mapped_column(String(20))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Each id sequence the batch moved: ``{name: {"before": n, "after": m}}``.
    sequences: Mapped[dict[str, Any]] = mapped_column(JSONB)
    #: The counts the import reported, for whoever reads the batch later.
    report: Mapped[dict[str, Any]] = mapped_column(JSONB)


class DeploymentMergeRow(Base):
    __tablename__ = "deployment_merge_rows"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    batch: Mapped[str] = mapped_column(
        String(100),
        ForeignKey(
            "deployment_merge_batches.batch",
            ondelete="CASCADE",
            name="fk_deployment_merge_rows_batch_deployment_merge_batches",
        ),
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer)
    table_name: Mapped[str] = mapped_column(String(100))
    #: The row's primary key here, ``{column: value}``.
    target_key: Mapped[dict[str, Any]] = mapped_column(JSONB)
    #: Its primary key in the other deployment; null for a row this one derived.
    source_key: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    previous: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    written: Mapped[dict[str, Any]] = mapped_column(JSONB)


class DeploymentMergeFile(Base):
    __tablename__ = "deployment_merge_files"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    batch: Mapped[str] = mapped_column(
        String(100),
        ForeignKey(
            "deployment_merge_batches.batch",
            ondelete="CASCADE",
            name="fk_deployment_merge_files_batch_deployment_merge_batches",
        ),
        index=True,
    )
    #: ``uploads`` for the uploads tree, else the blob store's location.
    store: Mapped[str] = mapped_column(String(20))
    key: Mapped[str] = mapped_column(Text)
