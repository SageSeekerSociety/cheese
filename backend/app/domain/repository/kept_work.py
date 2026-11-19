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


async def has_kept(
    project_id: uuid.UUID, session: AsyncSession, task_id: uuid.UUID
) -> bool:
    """Whether the task's checkout had uncommitted work when it was last kept:
    the ref exists only while there is some (a clean checkout deletes it). One
    forge read, and nothing on the sandbox, so it answers for a sandbox that
    is frozen or gone. False on a project that keeps nothing."""
    if not await keeps_work(project_id, session):
        return False
    data = await repository_data(
        project_id, session, "/git/commits/" + quote(kept_ref(task_id), safe="")
    )
    return bool(data)


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
    project_id: uuid.UUID,
    session: AsyncSession,
    task_id: uuid.UUID,
    branch: str,
    base: str,
) -> KeptWork | None:
    """The task's kept snapshot, or None: nothing uncommitted was kept, or the
    project keeps nothing. A forge that cannot answer raises, as any forge
    read does (`repository_data`).

    A task whose branch is not on the forge yet is checked out from its
    ``base``, at the commit the snapshot was cut from when the base has it
    (`_kept_start`), so that is when it is laid back."""
    if not await keeps_work(project_id, session):
        return None
    data = await repository_data(
        project_id, session, "/git/commits/" + quote(kept_ref(task_id), safe="")
    )
    if not data:
        return None
    parents = [parent.get("sha") for parent in data.get("parents") or []]
    parent = parents[0] if parents else None
    head = await branch_head(project_id, session, branch)
    if head is not None or parent is None:
        laid_back = parent is not None and parent == head
    else:
        compared = await repository_data(
            project_id,
            session,
            f"/compare/{quote(base, safe='')}...{parent}",
        )
        ahead = (compared or {}).get("total_commits", (compared or {}).get("ahead_by"))
        laid_back = ahead == 0
    return KeptWork(
        commit=data["sha"],
        kept_at=datetime.fromisoformat(data["commit"]["committer"]["date"]),
        laid_back=laid_back,
    )
