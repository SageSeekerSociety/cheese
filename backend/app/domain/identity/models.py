"""Agent identity as an *execution binding*, never a data column (fusion-design §2).

A user IS an agent iff it has an ``AgentBinding`` row — the "是不是 agent" flag is
**derived**, not stored on the user (照 reference identity.py). The binding names
what drives the agent: ``platform`` = our in-process agent runtime (今)， ``device``
= a self-hosted device connector (将来 P3). Business logic never branches on
``is_agent``; the frontend only reads it to draw an Agent 徽章.

芝士 is a real ``User`` row (handle ``cheese``) with one ``platform`` binding —
that binding, not a magic string, is what makes it an agent.

A binding also says WHOSE agent this is. ``owner_user_id`` NULL means the
platform's own 芝士; a value means a member delegated their access to an agent
they run themselves (see ``AgentToken``). The distinction matters at two points
where "an agent" used to mean "芝士": which member authors a room's AI blocks,
and who a ``<@all>`` broadcast may skip.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class AgentBindingKind:
    """Execution-binding kinds (not an Enum column — additive, forward-compatible)."""

    platform = "platform"  # driven by the platform agent runtime (current)
    # An agent a member runs themselves (local Claude Code, a bot) that reaches
    # the platform through a user-issued agent token. Same shape, different
    # driver — and, unlike ``platform``, it acts on one human's behalf.
    delegated = "delegated"
    # NOTE: no ``device`` kind — a self-hosted device is pure compute (a ComputePool
    # node), not an agent (execution-architecture v3). Agents run ON compute; they are
    # not minted BY it.


class AgentBinding(UuidPk, Timestamps, Base):
    __tablename__ = "agent_bindings"

    # One binding per agent user (a user is an agent iff a row exists here).
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), unique=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), default=AgentBindingKind.platform)
    # The human this agent acts for; NULL = the platform's own 芝士.
    owner_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=True, index=True
    )


class AgentToken(UuidPk, Timestamps, Base):
    """A credential a user issues so their own agent can act on the platform.

    The raw secret is never stored — only ``token_hash`` (see
    ``app.core.agent_tokens``). ``token_prefix`` is the clear-text head kept so
    a user can recognise a token in the list they can no longer read in full.

    Two user ids because they are different people: ``owner_user_id`` is the
    human who issued it and whose permissions bound it, ``agent_user_id`` is the
    companion agent-user the token authenticates as, so its blocks carry a handle
    of their own instead of being indistinguishable from the human's.
    """

    __tablename__ = "agent_tokens"

    owner_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    agent_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(64), default="")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    token_prefix: Mapped[str] = mapped_column(String(24), default="")
    # Every token expires: a credential handed to a process on someone's laptop
    # must not outlive the reason it was issued.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Answers "is this thing still in use?" before a user revokes it.
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
