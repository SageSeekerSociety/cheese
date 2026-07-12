"""Agent identity as an *execution binding*, never a data column (fusion-design §2).

A user IS an agent iff it has an ``AgentBinding`` row — the "是不是 agent" flag is
**derived**, not stored on the user (照 reference identity.py). The binding names
what drives the agent: ``platform`` = our in-process agent runtime (今)， ``device``
= a self-hosted device connector (将来 P3). Business logic never branches on
``is_agent``; the frontend only reads it to draw an Agent 徽章.

芝士 is a real ``User`` row (handle ``cheese``) with one ``platform`` binding —
that binding, not a magic string, is what makes it an agent.
"""

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class AgentBindingKind:
    """Execution-binding kinds (not an Enum column — additive, forward-compatible)."""

    platform = "platform"  # driven by the platform agent runtime (current)
    device = "device"  # driven by a self-hosted device connector (P3)


class AgentBinding(UuidPk, Timestamps, Base):
    __tablename__ = "agent_bindings"

    # One binding per agent user (a user is an agent iff a row exists here).
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), unique=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), default=AgentBindingKind.platform)
