"""Persistence for the agent plane (survives a backend restart).

- ``agent_screen``: the live agent screens, so after a restart the server can
  re-adopt the screens the (auto-reconnecting) cli still has running.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class AgentScreenRow(Base):
    """A live agent screen (an agent = a screen). Kept so a server restart can
    re-adopt what the cli still runs; deleted when the agent is closed."""

    __tablename__ = "agent_screen"

    sid: Mapped[str] = mapped_column(String(32), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    token: Mapped[str] = mapped_column(String(64), nullable=False)
    # Nullable: agents/chat are project-independent (project is a future wrapper). A
    # screen may run with no project.
    project_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    agent_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Claude Code's own conversation id, minted by us at launch (`claude --session-id
    # <uuid>`) so it is a single source of truth we control — never reverse-derived from
    # the device. Required for claudecode agents; it survives a device disconnect (the row
    # is kept for re-adopt), so `recreate --resume` can relaunch `claude --resume <id>`.
    # Nullable only for legacy rows started before this became mandatory.
    claude_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The working directory the agent was launched from — needed because Claude resolves
    # a session's transcript by the cwd's slug, so a resume must run from the same cwd.
    cwd: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
