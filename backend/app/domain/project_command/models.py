"""A project's named commands: a command someone fixed under a name, so that
anyone working on the project runs it again from their terminal.

Nothing here says what a command does or how a project runs: the command is
the text someone ran in a task, kept as it was run. A name is unique in its
project; fixing a command under a name the project already has replaces it.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import UuidPk

#: How long a name may be: a word or two on a button.
NAME_MAX = 40
#: How long a command may be.
COMMAND_MAX = 4000


class ProjectCommand(UuidPk, Base):
    __tablename__ = "project_commands"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_project_commands_name"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(NAME_MAX))
    command: Mapped[str] = mapped_column(Text)
    #: Who fixed it: a person's handle.
    fixed_by: Mapped[str] = mapped_column(String(128))
    fixed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
