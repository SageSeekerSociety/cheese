"""Task services — reading a room's threads, and the trees they work on."""

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.models import Block
from app.domain.room_task.models import (
    FILE_LOCK_TTL,
    HEAVY_LOCK_TTL,
    MAX_RESIDENT_TASKS_PER_ROOM,
    LockKind,
    Residency,
    RoomLock,
    Task,
    TaskStatus,
    WorkTree,
)
from app.domain.room_task.repositories import TaskRepository, WorkTreeRepository

#: A `running` row older than this is a ghost: the backend that was driving it
#: died, and nothing else will ever move it. Generous on purpose — a real turn
#: can be long, and freeing a slot out from under live work is worse than
#: leaving a dead one held for a while.
GHOST_RESIDENCY_AFTER = timedelta(hours=2)


class ResidencyService:
    """一个房间最多同时开 4 条后台子代理，多的排队。

    The cap counts what is RUNNING, not what exists: a thread that finished its
    turn is holding nothing, and it comes straight back if anyone speaks to it.
    See `Residency` for why that distinction is the whole design.
    """

    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = TaskRepository(session)

    async def admit(self, task: Task) -> bool:
        """Give *task* a slot if the room has one; queue it if not.

        Returns whether it may start now. A False is not a failure — the room is
        busy, the task keeps its thread, its brief and its owner, and it starts
        when a slot frees. Refusing instead would push a decision onto the
        dispatcher about a condition that clears by itself.
        """
        if await self._repo.count_resident(task.room_id) >= MAX_RESIDENT_TASKS_PER_ROOM:
            task.queued_at = task.queued_at or datetime.now(UTC)
            await self._session.flush()
            return False
        task.residency = Residency.running
        task.queued_at = None
        task.last_turn_at = datetime.now(UTC)
        await self._session.flush()
        return True

    async def touch(self, task: Task) -> None:
        """A turn is running here — hold the slot and reset the ghost clock."""
        task.residency = Residency.running
        task.queued_at = None
        task.last_turn_at = datetime.now(UTC)
        await self._session.flush()

    async def release(self, task: Task) -> Task | None:
        """The turn ended: free the slot, and hand it to whoever is next.

        Returns the task that just got the slot, so the caller can start it.
        None when nobody was waiting.
        """
        task.residency = Residency.idle
        await self._session.flush()
        return await self.dequeue(task.room_id)

    async def dequeue(self, room_id: uuid.UUID) -> Task | None:
        """Start the longest-waiting queued task, if a slot is now free.

        The ONLY place the queue moves. Spreading this over several call sites
        is how two of them race and admit five.
        """
        if await self._repo.count_resident(room_id) >= MAX_RESIDENT_TASKS_PER_ROOM:
            return None
        nxt = await self._repo.next_queued(room_id)
        if nxt is None:
            return None
        await self.admit(nxt)
        return nxt

    async def queue_position(self, task: Task) -> int:
        """1-based place in its room's queue; 0 when it is not queued."""
        if task.queued_at is None:
            return 0
        queued = await self._repo.list_queued(task.room_id)
        return next((i + 1 for i, t in enumerate(queued) if t.id == task.id), 0)

    async def holders(self, room_id: uuid.UUID) -> list[Task]:
        """Who is holding this room's slots.

        A room at its cap must be able to say WHO, with when each was last
        active — "排队中" on its own tells the person nothing about which thread
        to go and finish.
        """
        return await self._repo.list_resident(room_id)

    async def sweep_ghosts(self) -> list[Task]:
        """Free slots held by turns that died with the process driving them."""
        stale = await self._repo.list_stale_resident(
            datetime.now(UTC) - GHOST_RESIDENCY_AFTER
        )
        for task in stale:
            task.residency = Residency.idle
        if stale:
            await self._session.flush()
        return stale


class WorkTreeService:
    """一棵树 = 一个分支 = 一个 PR = 一批活."""

    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = WorkTreeRepository(session)

    async def get(self, tree_id: uuid.UUID) -> WorkTree | None:
        return await self._repo.get(tree_id)

    async def current(self, room_id: uuid.UUID) -> WorkTree | None:
        """The tree this room is writing to, or None while it is sealed."""
        return await self._repo.open_tree_for_room(room_id)

    async def ensure_open(
        self, *, project_id: uuid.UUID, room_id: uuid.UUID
    ) -> WorkTree:
        """The room's writable tree, starting the next batch if there isn't one.

        Being sealed is not a reason to refuse: sealing means "this batch's PR
        is flying, do not touch its content", and the answer to new work
        arriving is a NEW tree, not a queue. That is the whole reason a room may
        hold more than one — before it could, a PR in flight froze the room for
        as long as CI took.

        The very first tree carries the room's own id so that its branch,
        worktree directory, container workdir and tmux session are
        byte-for-byte the names they already had (migration `b8e2f4a90d33`).
        Later trees get fresh ids, and therefore fresh branches.
        """
        from app.domain.workspace import service as ws

        current = await self._repo.open_tree_for_room(room_id)
        if current is None:
            first = not await self._repo.list_for_room(room_id)
            current = await self._repo.add(
                project_id=project_id,
                room_id=room_id,
                tree_id=room_id if first else None,
            )
        # The workspace layer is sync and DB-free, so it cannot ask which tree a
        # room is on. Tell it — same arrangement `bind_room` uses for boxes.
        ws.bind_tree(room_id, current.id)
        return current

    async def seal(self, tree: WorkTree) -> WorkTree:
        return await self._repo.seal(tree)

    async def mark_merged(self, tree: WorkTree) -> WorkTree:
        return await self._repo.mark_merged(tree)

    async def tasks_on(self, tree_id: uuid.UUID) -> list[Task]:
        return await self._repo.list_tasks(tree_id)

    async def history(self, room_id: uuid.UUID) -> list[WorkTree]:
        return await self._repo.list_for_room(room_id)

    async def record_check(self, tree: WorkTree, *, ok: bool, detail: str) -> WorkTree:
        """Remember what the quick check said about this tree's content.

        It decides nothing — #296 settled that the PR's real CI is what does.
        The point is that a red check becomes VISIBLE on the card somebody is
        about to accept, because a check nobody sees is a check nobody runs.
        """
        tree.last_check_at = datetime.now(UTC)
        tree.last_check_ok = ok
        tree.last_check_detail = detail[:2000]
        await self._session.flush()
        return tree


class TaskService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = TaskRepository(session)

    async def get(self, task_id: uuid.UUID) -> Task | None:
        return await self._repo.get(task_id)

    async def open_thread(
        self,
        *,
        project_id: uuid.UUID,
        room_id: uuid.UUID,
        title: str,
        owner_handle: str | None,
        created_by: str | None,
        agent_instance_id: uuid.UUID | None,
    ) -> Task:
        """Open a new thread of work in a room, on the room's current tree.

        A service, not the repository, because the callers are in other domains
        (dispatch and 讨论升级 both live in `topic`), and a domain reaching into
        another's repository is what the import guard forbids.

        The tree is resolved HERE rather than asked of the caller: every caller
        wants the same answer — the batch this room is currently taking work
        into — and making each of them look it up is how two of them end up
        disagreeing about which batch a task belongs to.
        """
        from app.domain.workspace import service as ws

        tree = await WorkTreeService(self._session).ensure_open(
            project_id=project_id, room_id=room_id
        )
        task = await self._repo.add(
            project_id=project_id,
            room_id=room_id,
            tree_id=tree.id,
            title=title,
            owner_handle=owner_handle,
            created_by=created_by,
            agent_instance_id=agent_instance_id,
        )
        # A thread writes to its room's tree, with its siblings. Without this the
        # workspace layer would fall back to "the tree named by the place's own
        # id" and hand the thread an empty tree of its own — one worktree per
        # piece of work, which is exactly what this design removed.
        ws.bind_tree(task.id, tree.id)
        return task

    async def threads_for_room(
        self, room_id: uuid.UUID, *, limit: int | None = None
    ) -> list[tuple[Task, list[Block]]]:
        """Every thread in this room, each with its conversation, oldest first.

        `limit` caps EACH thread at its newest N blocks — the same
        bottom-anchored slice a chat window wants, applied per thread rather
        than across the room, because a room where one thread ran long would
        otherwise starve every other thread of its opening line. None returns
        everything, matching `/topics/{id}/blocks`: an agent reading history
        must not be silently truncated.

        Threads with nothing said in them come back with an empty list, not
        missing — a task that exists and has not been talked in is a real
        answer, and dropping it would make the room's thread count depend on
        whether anyone had spoken yet.
        """
        tasks = await self._repo.list_for_room(room_id)
        conversations = await self._repo.conversations_for_tasks([t.id for t in tasks])
        out: list[tuple[Task, list[Block]]] = []
        for task in tasks:
            blocks = conversations.get(task.id, [])
            out.append((task, blocks[-limit:] if limit is not None else blocks))
        return out


class ClaimService:
    """谁在碰哪些路径 —— 声明，以及声明之间的冲突。

    A claim is a path. **A claim ending in `/` is a directory; anything else is
    a file.** That one rule is what lets the two answers stay far apart:

    - the same FILE claimed twice is refused, because two threads writing one
      file is the case with no other defence — the second write wins in silence;
    - overlapping DIRECTORIES are a warning, because two threads working under
      `backend/app/domain/review/` is ordinary and refusing it would make the
      rule something people route around.

    Claims are checked against the siblings on the same TREE, not the same room:
    files are what conflict, and the tree is what holds the files.
    """

    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = TaskRepository(session)
        self._trees = WorkTreeRepository(session)

    @staticmethod
    def normalise(paths: Iterable[str]) -> list[str]:
        """Trim, drop empties, drop duplicates, keep the order given."""
        seen: dict[str, None] = {}
        for raw in paths:
            path = raw.strip().lstrip("./")
            if path:
                seen.setdefault(path, None)
        return list(seen)

    @staticmethod
    def _is_dir(path: str) -> bool:
        return path.endswith("/")

    @classmethod
    def _overlaps(cls, a: str, b: str) -> bool:
        """Do these two claims cover any of the same ground?"""
        if a == b:
            return True
        if cls._is_dir(a) and b.startswith(a):
            return True
        return cls._is_dir(b) and a.startswith(b)

    async def check(
        self, *, tree_id: uuid.UUID, paths: list[str], exclude_task_id: uuid.UUID | None
    ) -> tuple[list[str], list[str]]:
        """(refusals, warnings) for claiming *paths* on *tree_id*.

        A refusal names the other piece of work, because "conflict" without a
        name leaves the caller nothing to do: the fix is to narrow one of the
        two claims or to wait for the other, and both need to know which.
        """
        refusals: list[str] = []
        warnings: list[str] = []
        for other in await self._trees.list_tasks(tree_id):
            if other.id == exclude_task_id or other.status is TaskStatus.closed:
                continue
            for mine in paths:
                for theirs in other.claimed_paths or []:
                    if not self._overlaps(mine, theirs):
                        continue
                    if mine == theirs and not self._is_dir(mine):
                        refusals.append(f"「{other.title}」已经声明了同一个文件 {mine}")
                    else:
                        warnings.append(
                            f"「{other.title}」也在 {theirs} 底下干活"
                            f"（你声明的是 {mine}）"
                        )
        return refusals, warnings

    async def claim(
        self, task: Task, paths: Iterable[str]
    ) -> tuple[list[str], list[str]]:
        """Add *paths* to what this task says it will touch.

        Additive: a claim grows as work reaches files nobody predicted, and
        replacing it would silently drop the ground already agreed on.
        Refusals are returned rather than raised — the caller has to show them
        AND the warnings, and an exception carries only one of the two.
        """
        wanted = self.normalise(paths)
        refusals, warnings = await self.check(
            tree_id=task.tree_id, paths=wanted, exclude_task_id=task.id
        )
        if refusals:
            return refusals, warnings
        task.claimed_paths = self.normalise([*(task.claimed_paths or []), *wanted])
        await self._session.flush()
        return [], warnings

    async def unclaimed(self, task: Task, touched: Iterable[str]) -> list[str]:
        """Which of the paths actually touched were never claimed.

        A claim is an intention and a snapshot is a fact; if nothing ever
        compares them the claim is decoration. This is reported, never blocked —
        finding out that work went somewhere unexpected is useful, and stopping
        it after the fact would only lose the work.
        """
        claimed = task.claimed_paths or []
        return [
            path
            for path in self.normalise(touched)
            if not any(self._overlaps(path, c) for c in claimed)
        ]

    async def unclaimed_by_topic(
        self, topic_id: uuid.UUID, touched: Iterable[str]
    ) -> list[str]:
        """:meth:`unclaimed`, asked by topic id instead of by Task.

        Callers outside this domain hold a topic id and no Task. Without this
        they reach for `TaskRepository` themselves, which is exactly the
        cross-domain repository touch the import guard exists to stop.

        A topic that is a room's own line, or work that claimed nothing, has no
        expectation to have exceeded — both answer with an empty list.
        """
        task = await self._repo.get(topic_id)
        if task is None or not task.claimed_paths:
            return []
        return await self.unclaimed(task, touched)


class RoomLockService:
    """整块覆盖一个文件、跑重活 —— 一次一个。

    Narrow on purpose. It does not make concurrent writing safe in general: a
    lock around a write cannot prevent a lost update, because the read that the
    write is based on happened before the lock existed. It covers the two cases
    that have no other defence — a whole-file overwrite (an `Edit` defends
    itself; a `Write` cannot) and the things that fight over the machine rather
    than the tree.
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def acquire(
        self,
        *,
        room_id: uuid.UUID,
        kind: LockKind,
        resource: str = "",
        holder_task_id: uuid.UUID | None,
    ) -> tuple[bool, str]:
        """Take the lock, or say who has it.

        Never waits. Blocking would spend a whole turn's compute sitting still,
        and the caller has something better to do with the answer — write a
        different file, use `Edit`, come back next turn.
        """
        now = datetime.now(UTC)
        existing = (
            await self._session.scalars(
                select(RoomLock).where(
                    RoomLock.room_id == room_id,
                    RoomLock.kind == kind,
                    RoomLock.resource == resource,
                )
            )
        ).first()
        if existing is not None:
            if existing.expires_at > now:
                if existing.holder_task_id == holder_task_id:
                    existing.expires_at = now + self._ttl(kind)
                    await self._session.flush()
                    return True, ""
                return False, await self._who(existing)
            # Expired: whoever held it is not coming back.
            await self._session.delete(existing)
            await self._session.flush()
        self._session.add(
            RoomLock(
                room_id=room_id,
                kind=kind,
                resource=resource,
                holder_task_id=holder_task_id,
                acquired_at=now,
                expires_at=now + self._ttl(kind),
            )
        )
        await self._session.flush()
        return True, ""

    async def release(
        self,
        *,
        room_id: uuid.UUID,
        kind: LockKind,
        resource: str = "",
        holder_task_id: uuid.UUID | None,
    ) -> bool:
        """Give it back. Releasing a lock somebody else holds does nothing."""
        existing = (
            await self._session.scalars(
                select(RoomLock).where(
                    RoomLock.room_id == room_id,
                    RoomLock.kind == kind,
                    RoomLock.resource == resource,
                )
            )
        ).first()
        if existing is None or existing.holder_task_id != holder_task_id:
            return False
        await self._session.delete(existing)
        await self._session.flush()
        return True

    async def sweep_expired(self) -> int:
        """Take back every lock whose holder never came home."""
        stale = list(
            (
                await self._session.scalars(
                    select(RoomLock).where(RoomLock.expires_at <= datetime.now(UTC))
                )
            ).all()
        )
        for lock in stale:
            await self._session.delete(lock)
        if stale:
            await self._session.flush()
        return len(stale)

    @staticmethod
    def _ttl(kind: LockKind) -> timedelta:
        return FILE_LOCK_TTL if kind is LockKind.file else HEAVY_LOCK_TTL

    async def _who(self, lock: RoomLock) -> str:
        if lock.holder_task_id is None:
            return "这个房间自己正在改它"
        task = await TaskRepository(self._session).get(lock.holder_task_id)
        return f"「{task.title}」正在改它" if task else "另一条活正在改它"
