"""What a task's sandbox kept of the work it had not committed.

A task checkout's uncommitted files are kept on the project's forge, off the
sandbox (`sandbox/cheese` `_keep_working_copy`): one commit at the task's
``KEPT_REFS`` ref whose tree is the checkout's files and whose parent is the
head they were on. A new checkout of the task lays them back over that head,
and only over that head (`_lay_back_kept`). So whether the work comes back is
known before any checkout is made: the kept commit's parent against the
branch's head now. This reads that from the forge, for the teammate told it is
in a new sandbox and for a person opening the task's files meanwhile.

Only a repository the platform hosts keeps anything; a project bound to GitHub
keeps what it pushed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.project.forge import binding_for_project, branch_head, repository_data

#: The CLI's own (`sandbox/cheese` `KEPT_REFS`): one ref a task, out of
#: `refs/heads` and `refs/tags`.
KEPT_REFS = "refs/cheese/kept/"


def kept_ref(task_id: uuid.UUID) -> str:
    return f"{KEPT_REFS}{task_id}"


async def keeps_work(project_id: uuid.UUID, session: AsyncSession) -> bool:
    """Whether the project's task checkouts keep uncommitted work off their
    sandbox: its repository is the platform's own."""
    binding = await binding_for_project(project_id, session)
    return binding is not None and binding.kind == "forgejo"


@dataclass(frozen=True)
class KeptWork:
    """A task's kept snapshot: the commit, when it was kept, and whether a new
    checkout of the branch lays it back (its parent is the branch's head)."""

    commit: str
    kept_at: datetime
    laid_back: bool

    def stamp(self) -> str:
        """When it was kept, written so its zone cannot be missed."""
        return self.kept_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


async def kept_work(
    project_id: uuid.UUID, session: AsyncSession, task_id: uuid.UUID, branch: str
) -> KeptWork | None:
    """The task's kept snapshot, or None: nothing uncommitted was kept, or the
    project keeps nothing. A forge that cannot answer raises, as any forge
    read does (`repository_data`)."""
    if not await keeps_work(project_id, session):
        return None
    data = await repository_data(
        project_id, session, "/git/commits/" + quote(kept_ref(task_id), safe="")
    )
    if not data:
        return None
    parents = [parent.get("sha") for parent in data.get("parents") or []]
    head = await branch_head(project_id, session, branch)
    return KeptWork(
        commit=data["sha"],
        kept_at=datetime.fromisoformat(data["commit"]["committer"]["date"]),
        laid_back=bool(parents) and parents[0] == head,
    )
