"""What a task's sandbox kept of the work it had not committed.

A task checkout's uncommitted files are kept off the sandbox
(`sandbox/cheese` `_keep_working_copy`): one commit at the task's
``KEPT_REFS`` ref whose tree is the checkout's files and whose parent is the
head they were on. A new checkout of the task lays them back over that head,
and only over that head (`_lay_back_kept`). So whether the work comes back is
known before any checkout is made: the kept commit's parent against the
branch's head now. This reads that, for the teammate told it is in a new
sandbox and for a person opening the task's files meanwhile.

Where the commit is kept depends on whose repository it is. The platform's
own Forgejo holds it at the ref itself. Any other repository is someone
else's, and nothing uncommitted is put there: the machine packs the commit's
new objects into a git bundle and hands it to the platform, which keeps it in
the private bucket with one ``KeptBundle`` row per task (`keep_bundle`), and a
new checkout fetches it from there (`bundle_of`).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.storage import private_storage
from app.domain.project.forge import binding_for_project, branch_head, repository_data
from app.domain.repository.models import KeptBundle

#: The CLI's own (`sandbox/cheese` `KEPT_REFS`): one ref a task, out of
#: `refs/heads` and `refs/tags`.
KEPT_REFS = "refs/cheese/kept/"
#: The CLI's own (`sandbox/cheese` `KEPT_COPIES`): where a new checkout
#: fetches a task's snapshot to.
KEPT_COPIES = "refs/remotes/origin/cheese-kept/"
#: Where the bundles are in the private bucket, one object per snapshot.
BUNDLES = "kept-bundles"
#: The largest bundle taken. A snapshot holds at most 80 MB of files
#: (`sandbox/cheese` `KEPT_TOTAL_MAX_BYTES`), compressed in the bundle; the
#: rest is room for the trees and the header.
BUNDLE_MAX_BYTES = 96 * 1024 * 1024


def kept_ref(task_id: uuid.UUID) -> str:
    return f"{KEPT_REFS}{task_id}"


async def keeps_work(project_id: uuid.UUID, session: AsyncSession) -> bool:
    """Whether the project's task checkouts keep uncommitted work off their
    sandbox: every project with a repository does."""
    return await binding_for_project(project_id, session) is not None


async def _on_forge(project_id: uuid.UUID, session: AsyncSession) -> bool:
    """Whether the snapshot is kept on the project's forge: its repository is
    the platform's own. Otherwise the platform keeps it (`KeptBundle`)."""
    binding = await binding_for_project(project_id, session)
    return binding is not None and binding.kind == "forgejo"


async def has_kept(
    project_id: uuid.UUID, session: AsyncSession, task_id: uuid.UUID
) -> bool:
    """Whether the task's checkout had uncommitted work when it was last kept:
    the snapshot exists only while there is some (a clean checkout removes
    it). One read, and nothing on the sandbox, so it answers for a sandbox
    that is frozen or gone. False on a project that keeps nothing."""
    if not await keeps_work(project_id, session):
        return False
    if not await _on_forge(project_id, session):
        return await session.get(KeptBundle, task_id) is not None
    data = await repository_data(
        project_id, session, "/git/commits/" + quote(kept_ref(task_id), safe="")
    )
    return bool(data)


@dataclass(frozen=True)
class KeptWork:
    """A task's kept snapshot: the commit, when it was kept, whether a new
    checkout of the branch lays it back (its parent is the branch's head),
    where a checkout finds it, and whether the forge holds it, so its files
    can be read there."""

    commit: str
    kept_at: datetime
    laid_back: bool
    ref: str
    on_forge: bool

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
    if await _on_forge(project_id, session):
        data = await repository_data(
            project_id, session, "/git/commits/" + quote(kept_ref(task_id), safe="")
        )
        if not data:
            return None
        parents = [parent.get("sha") for parent in data.get("parents") or []]
        commit, parent, ref, on_forge = (
            data["sha"],
            parents[0] if parents else None,
            kept_ref(task_id),
            True,
        )
        kept_at = datetime.fromisoformat(data["commit"]["committer"]["date"])
    else:
        row = await session.get(KeptBundle, task_id)
        if row is None:
            return None
        commit, parent, ref, on_forge = (
            row.commit,
            row.cut_from,
            f"{KEPT_COPIES}{task_id}",
            False,
        )
        kept_at = row.saved_at
    return KeptWork(
        commit=commit,
        kept_at=kept_at,
        laid_back=await _laid_back(project_id, session, parent, branch, base),
        ref=ref,
        on_forge=on_forge,
    )


async def _laid_back(
    project_id: uuid.UUID,
    session: AsyncSession,
    parent: str | None,
    branch: str,
    base: str,
) -> bool:
    head = await branch_head(project_id, session, branch)
    if head is not None or parent is None:
        return parent is not None and parent == head
    compared = await repository_data(
        project_id,
        session,
        f"/compare/{quote(base, safe='')}...{parent}",
    )
    ahead = (compared or {}).get("total_commits", (compared or {}).get("ahead_by"))
    return ahead == 0


class NotABundle(ValueError):
    """What was sent is not one task's snapshot as a git bundle."""


_OBJECT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def snapshot_in(bundle: bytes, task_id: uuid.UUID) -> str:
    """The snapshot commit a bundle carries, read off its header: exactly one
    ref, the task's ``KEPT_REFS`` one. Its prerequisites are what the
    project's repository already had when it was packed, which is the
    machine's to know."""
    header, blank, _ = bundle.partition(b"\n\n")
    lines = header.decode("utf-8", errors="replace").split("\n")
    if not blank or lines[0] not in ("# v2 git bundle", "# v3 git bundle"):
        # i18n-exempt: only the platform's own CLI sends a bundle; no person reads it
        raise NotABundle("not a git bundle")
    refs = [
        tuple(line.split(" ", 1))
        for line in lines[1:]
        if not line.startswith(("@", "-"))  # a v3 capability, a prerequisite
    ]
    if (
        len(refs) != 1
        or len(refs[0]) != 2
        or refs[0][1] != kept_ref(task_id)
        or not _OBJECT.fullmatch(refs[0][0])
    ):
        # i18n-exempt: only the platform's own CLI sends a bundle; no person reads it
        raise NotABundle("not this task's kept snapshot")
    return refs[0][0]


async def keep_bundle(
    session: AsyncSession, task_id: uuid.UUID, bundle: bytes, cut_from: str
) -> KeptBundle:
    """Keep ``bundle`` as the task's snapshot, cut from the commit
    ``cut_from`` (the head its files were on), in place of the one before.
    The object is written before the row names it and the old one is removed
    after, so a failure part way leaves the previous snapshot whole."""
    commit = snapshot_in(bundle, task_id)
    if not _OBJECT.fullmatch(cut_from):
        # i18n-exempt: only the platform's own CLI sends this; no person reads it
        raise NotABundle("not a commit the snapshot was cut from")
    bucket = private_storage()
    now = datetime.now(UTC)
    key = f"{BUNDLES}/{task_id}/{int(now.timestamp())}-{uuid.uuid4()}.bundle"
    await bucket.put(key, bundle)
    row = await session.get(KeptBundle, task_id, with_for_update=True)
    old = row.key if row is not None else None
    if row is None:
        row = KeptBundle(task_id=task_id)
        session.add(row)
    row.cut_from, row.commit, row.key = cut_from, commit, key
    row.size, row.saved_at = len(bundle), now
    await session.commit()
    if old:
        await bucket.delete(old)
    return row


async def drop_bundle(session: AsyncSession, task_id: uuid.UUID) -> None:
    """The task's checkout has nothing uncommitted: its snapshot goes."""
    row = await session.get(KeptBundle, task_id, with_for_update=True)
    if row is None:
        return
    key = row.key
    await session.delete(row)
    await session.commit()
    await private_storage().delete(key)


async def bundle_of(session: AsyncSession, task_id: uuid.UUID) -> bytes | None:
    """The task's snapshot as the machine handed it in, or None when there is
    none."""
    row = await session.get(KeptBundle, task_id)
    if row is None:
        return None
    return await private_storage().download(row.key)
