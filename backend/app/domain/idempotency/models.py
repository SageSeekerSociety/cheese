"""Idempotency key model.

One row = one side effect that has already happened. The row and the effect are
written in the SAME transaction, so "the key exists" and "the effect happened"
can never disagree — which is the whole point: a Redis key written next to a DB
write has a window where the process can die between the two, and that window is
exactly what a re-sent turn (重发, re-running under the same continuation) walks
into.

See ``store.claim`` for the contract.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import UuidPk


def _now() -> datetime:
    return datetime.now(UTC)


class IdempotencyKey(UuidPk, Base):
    __tablename__ = "idempotency_keys"

    #: sha256 hex of (continuation_id, action, payload) — see ``keys.action_key``.
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    #: Which side effect this guards ("message"/"split"/...). Debug/observability
    #: only — the key alone decides, this just makes a row readable.
    action: Mapped[str] = mapped_column(String(32))
    #: Topic or project the effect landed in. Same rationale as ``action``.
    scope_id: Mapped[str] = mapped_column(String(64), index=True)
    #: What the first execution produced, replayed verbatim to a caller whose
    #: claim loses. NULL when the effect has no payload worth returning (or when
    #: the winner has not finished writing it yet — see ``store.claim``).
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )


__all__ = ["IdempotencyKey"]
