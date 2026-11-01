"""Sandboxes are destroyed once idle, on every machine.

A sandbox on one of the platform's hosts and one on a person's computer have
the same lifecycle; only the idle time differs, and that is the spec's
(``agent.environment.Spec.idle_s``).

A sandbox is **idle** when its room runs no turn and its session has asked for
no tool for its spec's idle time — counted from the later of its last
tool call (``SandboxHome.active_at``) and the end of the last turn in the
conversation its session works in. A command the executor is still running for
the session — a Bash call sent to the background, a long build or test whose
result the next turn reads (``running_commands``) — keeps an idle sandbox up
too, but only until the spec's ``background_cap_s`` after that activity. A cap
spent, the command is stopped rather than the sandbox destroyed: the session
wakes to read its result in the home it left, so it keeps the work and the
place its next tool call lands. A command the machine no longer runs is
activity — the session is waking to it — and gives the home a fresh idle
window instead of counting as silence. A process the agent detached itself
(``nohup … &``, a dev server) is not work in progress and keeps nothing up. A
line that has handed its delivery over and is waiting on the checks the
platform is watching for its card
(``review.queries.waiting_on_checks``) is not idle either, on the same clock:
the poller wakes its session when they finish, and the sandbox it comes back
to should be this one rather than a new one and the documents read again. A
home of no session (a room's directory from
before session leases) is measured by its own activity alone. A sandbox whose
project has run out
of credits is destroyed as soon as its room runs no turn (``metering``), idle
or not.

**Frozen**, an idle sandbox on one of the platform's hosts is kept instead of
destroyed (`cloud_sandbox_freeze`): one checkpoint of its session runs first,
then its processes are stopped (`cgroup.freeze`), it is allowed the host's swap
file (`memory.swap.max`) and asked to give back what memory the kernel can take
(`memory.reclaim`). Its home, its work and its executor wait on the host, it is
not billed, and its session's next tool call wakes this same sandbox — same
host, same home, same processes (`session_work._install`) — rather than
building a new one. A frozen sandbox holds its host's slot while it sleeps, so
it cannot be kept for good: past `cloud_sandbox_frozen_keep_s` it is destroyed
as any idle sandbox always was. A slot the pool needs, a disk running short and
a host being rebuilt take a frozen sandbox sooner, oldest first. A whole cloud
VM and a sandbox on a person's own computer are not frozen: they keep the
lifecycle below.

**Destroyed**, a sandbox's executor and everything still running in its home
are stopped and the home is deleted from its machine (``sandbox_home.destroy``);
then its row goes, and on a host with it the host's slot. A machine that is
offline is skipped until it is back. Nothing of the sandbox is kept:
what lasts is the conversation and the commits the session pushed. The session's next tool call places it in a
new sandbox, which starts like any new one, and that call's result
tells the agent it is in a new sandbox (``LOST_KEY``).

While the host removes the home, the row is marked stopped (``stopped_at``):
a tool call that arrives meanwhile waits for it to go (``SandboxRemoving``)
rather than start a sandbox over a directory being deleted. A removal that
does not finish — the host failed it, this process stopped — leaves the mark,
and a later sweep removes the home again once ``STOP_HOLD`` has passed.

A session's whole cloud VM is released by the same idle measure, held for
its spec's idle time (``cloud_vm``), on the same clock
(``runner.SandboxSweeper``).

When the pool is pressed — no host that may take a sandbox has a free slot,
or a session is waiting on a shared host's slot — an idle sandbox on a
platform host is destroyed after ``cloud_sandbox_idle_stop_pressed_s``
instead, so the slot goes to whoever is waiting on it. A sandbox on a
person's own computer keeps its spec's idle time whatever the pool's state;
a whole cloud VM is neither this sweep's nor a user of the shared slots
(``cloud_vm``), so waiting on one presses nothing.
"""

import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import ColumnElement, and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.agent import execution, resource_cleanup, turn_inputs
from app.domain.agent.device_hub import device_hub
from app.domain.agent.environment import STANDARD, spec_of
from app.domain.agent.models import AgentTurn
from app.domain.agent_session.models import LOST_KEY, THAW_KEY, AgentSession
from app.domain.machine import holder as holders
from app.domain.machine import metering, sandbox_home
from app.domain.machine.models import CloudHost, SandboxHome
from app.domain.machine.progress import publish_line, tell_released, tell_unpaid
from app.domain.machine.reaper import accepting, free_slots
from app.domain.machine.repositories import CloudHostRepository
from app.domain.review.queries import waiting_on_checks

logger = logging.getLogger("cheese.machine.lifecycle")


class SandboxRemoving(Exception):
    """The session's sandbox is being destroyed; the tool call waits for that
    to finish and then gets a new one."""


#: How long a removal may hold a home before a later sweep removes it again:
#: past the longest the host is given for it.
STOP_HOLD = timedelta(minutes=3)
#: A sandbox whose background command keeps it up is asked again this often.
RECHECK = timedelta(minutes=1)
#: A command the machine had running at the last ask, and does not now, ended
#: within this window: the session is waking to read it, not sitting idle.
WOKEN_FOR = timedelta(minutes=2)
#: The stop a command past its background cap is sent, by the POSIX number the
#: executor knows it by (``runtime.STOP_SIGNALS``: SIGTERM).
TERM = 15
#: Sandboxes destroyed per sweep.
DESTROYS_PER_SWEEP = 20

# When each home kept up by its background work was last asked, in this
# process, and whether the machine had commands running for it then: the sweep
# runs every few seconds, and asking is a call to the host. The second half
# tells a command that just ended from a machine that had nothing running all
# along.
_asked: dict[uuid.UUID, tuple[datetime, bool]] = {}


class SandboxHomeError(RuntimeError):
    """The host could not do what was asked to the home."""


def program(action: str, **request) -> str:
    """The program a host runs for ``action`` on one home: the cleanup's
    helpers, then ``sandbox_home``, then the call."""
    cleanup = Path(resource_cleanup.__file__).read_text()
    call = json.dumps({"action": action, **request})
    return (
        "import json\n"
        "cleanup = {'__name__': 'cheese_resource_cleanup'}\n"
        f"exec({cleanup!r}, cleanup)\n"
        + Path(sandbox_home.__file__).read_text()
        + f"\nmain(cleanup, json.loads({call!r}))\n"
    )


async def run_on_host(hub, device_id: str, action: str, timeout: float, **request):
    result = await hub.exec(
        device_id,
        ["python3", "-"],
        stdin=program(action, **request),
        timeout=timeout,
    )
    if result.get("exit") != 0 or result.get("truncated"):
        raise SandboxHomeError(
            str(result.get("stderr") or f"sandbox {action} failed")[-1500:]
        )
    return json.loads(str(result["stdout"]).strip().splitlines()[-1])


async def thaw_on_host(hub, device_id: str, resource: str) -> None:
    """Wake a frozen sandbox on its host: let it use no swap (`memory.swap.max`
    back to 0, so it does not sit in the swap file while it runs) and resume
    its processes (`sandbox_home.thaw`). Raises ``SandboxHomeError`` when the
    host cannot thaw it; a host that cannot set the swap limit still thaws.

    Called with no database lock held (`session_work._install` and the freeze
    below): it is a round trip to the machine.
    """
    try:
        answer = await run_on_host(
            hub, device_id, "swap", timeout=30, name=resource, mib=0
        )
        if answer.get("swap_error"):
            logger.warning(
                "setting sandbox %s's swap back to 0 failed: %s",
                resource,
                answer["swap_error"],
            )
    except (RuntimeError, TimeoutError):
        logger.warning("setting sandbox %s's swap back to 0 failed", resource)
    answer = await run_on_host(hub, device_id, "thaw", timeout=30, names=[resource])
    if answer.get("thaw_error"):
        raise SandboxHomeError(str(answer["thaw_error"]))


async def _checkpoint(hub, lease: dict, start) -> bool:
    """The session's one checkpoint, then the sandbox is stopped.

    A frozen sandbox's processes do not run, so the checkpoint is impossible
    once it is frozen — and a session woken out of a freeze would otherwise
    have work in it that was never pushed. It is the same command a turn's
    Stop runs (`session_work.checkpoint`) and fails the same way: whatever
    happens, the sandbox is still frozen."""
    # deferred-import: `session_work` imports `lifecycle` for `SandboxRemoving`
    from app.domain.machine import session_work

    try:
        return await session_work.checkpoint(lease, start)
    except Exception:
        logger.warning("checkpoint before freezing failed", exc_info=True)
        return False


async def frozen_sessions(db, session_ids) -> set[uuid.UUID]:
    """Which of these sessions' homes are frozen. A frozen sandbox cannot run
    the checkpoint a room's cleanup or an environment switch would ask it for:
    it was checkpointed before it was frozen and nothing ran since
    (`session_work.checkpoint_room`, `switch_environment`)."""
    return set(
        await db.scalars(
            select(SandboxHome.session_id).where(
                SandboxHome.session_id.in_(list(session_ids)),
                SandboxHome.frozen_at.is_not(None),
            )
        )
    )


async def thaw_if_asked(db, hub, *, session_id, device_id: str, work_resource) -> None:
    """Wake a sandbox a tool call has already asked for (`THAW_KEY`), before
    its executor is asked anything (`session_work._install`, under the claim,
    with no database lock held).

    The row was marked woken under the pool lock (`HostPool.place`); its
    processes are still stopped until this runs, so a call sent now would wait
    on the freezer. A failed thaw is raised, not swallowed: the attempt lapses
    and the next one tries again, which is better than an executor nobody can
    reach for a minute.
    """
    marker = await db.scalar(
        select(AgentSession.execution_request).where(AgentSession.id == session_id)
    )
    await db.commit()
    thawing = (marker or {}).get(THAW_KEY)
    if not thawing or str(thawing) != str(work_resource):
        return
    await thaw_on_host(hub, device_id, str(work_resource))
    row = await db.scalar(
        select(AgentSession).where(AgentSession.id == session_id).with_for_update()
    )
    if row is not None and (row.execution_request or {}).get(THAW_KEY) == thawing:
        left = dict(row.execution_request or {})
        left.pop(THAW_KEY, None)
        row.execution_request = left
    await db.commit()


def _idle_for(spec: str) -> timedelta:
    """How long a sandbox of ``spec`` is kept idle; a spec that no longer
    exists is held to the standard one's."""
    known = spec_of(spec) or spec_of(STANDARD)
    assert known is not None
    return timedelta(seconds=known.idle_s)


def _background_cap(spec: str) -> timedelta:
    """How long a command still running for the session keeps an idle sandbox
    of ``spec`` up; a spec that no longer exists is held to the standard one's.
    A whole VM's idle release outlasts a sandbox's, so its cap is longer —
    a cap shorter than the idle time would hold nothing up."""
    known = spec_of(spec) or spec_of(STANDARD)
    assert known is not None
    return timedelta(seconds=known.background_cap_s)


def _pressed_idle() -> timedelta:
    """How long a sandbox on a platform host is kept idle while the pool has
    no room for another one (`cloud_sandbox_idle_stop_pressed_s`)."""
    return timedelta(seconds=settings.cloud_sandbox_idle_stop_pressed_s)


def _frozen_keep() -> timedelta:
    """How long a frozen sandbox is kept before it is destroyed anyway
    (`cloud_sandbox_frozen_keep_s`): it holds its host's slot the whole
    time."""
    return timedelta(seconds=settings.cloud_sandbox_frozen_keep_s)


def frozen_eviction_key(home) -> tuple:
    """What ranks a frozen sandbox for eviction: the one frozen longest ago
    first. A function rather than a column order, so what has a preview can be
    kept for last in a later step: return a tuple that sorts those after."""
    return (home.frozen_at, home.id)


#: How long an open turn's interval may stand and still count as its session at
#: work. A process killed inside a turn leaves its row open and nothing closes
#: it (`AgentWorkRunner.ORPHAN_STALE_S`, which this module cannot import): past
#: that age the platform has already given up on the turn, and the row must not
#: hold a machine for good.
STALE_TURN = timedelta(hours=2)


def _of_session(session_id, column, correlate):
    """``column`` of the session ``session_id`` names, as a scalar subquery:
    for the two things a turn has to be of to be this session's work."""
    query = select(column).where(AgentSession.id == session_id)
    if correlate is not None:
        query = query.correlate(correlate)
    return query.scalar_subquery()


def _mine(conversation, seat) -> ColumnElement[bool]:
    """The clauses saying a turn belongs to the session ``conversation`` and
    ``seat`` name.

    The conversation is the session's own, never its room's: every task of a
    channel hangs under the room, and reading the room kept every other task's
    sandbox in it up for days. The seat is the session's own too — another
    teammate's turn in the same conversation is not this session's work — and a
    row whose seat was never recorded counts as anybody's, which is how every
    other reader counts one (``agent.turn.state.authority``).
    """
    return and_(
        AgentTurn.conversation_id == conversation,
        or_(AgentTurn.agent_handle.is_(None), AgentTurn.agent_handle == seat),
    )


def _own_turns(session_id, *, correlate=None) -> ColumnElement[bool]:
    """``_mine`` for the session ``session_id`` names, for a query already over
    the homes."""
    return _mine(
        _of_session(session_id, AgentSession.conversation_id, correlate),
        _of_session(session_id, AgentSession.agent_handle, correlate),
    )


class SandboxLifecycle:
    def __init__(
        self,
        session: AsyncSession,
        hub=None,
        *,
        recovering: Callable[[uuid.UUID], bool] | None = None,
    ) -> None:
        self._session = session
        self._repo = CloudHostRepository(session)
        self._hub = hub or device_hub
        self._recovering = recovering

    async def sweep(self) -> dict[str, int]:

        destroyed = await self.destroy_idle()
        for home in await metering.unpaid_sandboxes(self._session):
            if self._hub.is_online(home.device_id) and await self._destroy(
                home.id,
                home.topic_id if home.session_id is not None else None,
                home.device_id,
                None,
            ):
                destroyed += 1
        return {"destroyed": destroyed}

    async def destroy_idle(self) -> int:
        """Destroy every idle sandbox, and finish every removal left
        unfinished; returns how many were destroyed."""
        now = datetime.now(UTC)
        # The shortest idle time of any spec, to narrow the rows; each is then
        # held to its own spec's.
        idle_for = _idle_for(STANDARD)
        pressed = await self._pressed()
        # A pressed pool takes its cloud sandboxes back sooner.
        narrow = min(idle_for, _pressed_idle()) if pressed else idle_for

        # A turn of the session's own, running or ended within the idle time,
        # keeps that sandbox up. Decided here, not after a limit: busy homes
        # would otherwise take every place in the batch.
        session_active = (
            select(AgentTurn.id)
            .where(
                _own_turns(SandboxHome.session_id, correlate=SandboxHome),
                or_(
                    and_(
                        AgentTurn.stopped_at.is_(None),
                        AgentTurn.started_at > now - STALE_TURN,
                    ),
                    AgentTurn.stopped_at > now - narrow,
                ),
            )
            .exists()
        )
        rows = await self._session.execute(
            select(
                SandboxHome.id,
                SandboxHome.session_id,
                SandboxHome.topic_id,
                SandboxHome.host_id,
                SandboxHome.active_at,
                SandboxHome.frozen_at,
                SandboxHome.stopped_at,
                SandboxHome.spec,
                SandboxHome.resource_id,
                func.coalesce(CloudHost.device_id, SandboxHome.device_id).label(
                    "device_id"
                ),
            )
            .outerjoin(CloudHost, CloudHost.id == SandboxHome.host_id)
            .where(
                # On a person's computer, or on a live host.
                or_(
                    SandboxHome.host_id.is_(None),
                    CloudHost.released_at.is_(None)
                    & CloudHost.device_id.is_not(None)
                    # A session's whole cloud VM is released, not destroyed
                    # here (``cloud_vm``).
                    & CloudHost.whole_machine.is_(False),
                ),
                or_(
                    # A frozen sandbox is looked at whatever its idle clock
                    # says: it is not running, so it is not pinged, and what
                    # may end it is its keep time or an eviction elsewhere.
                    SandboxHome.frozen_at.is_not(None),
                    SandboxHome.stopped_at < now - STOP_HOLD,
                    (SandboxHome.stopped_at.is_(None))
                    & (SandboxHome.active_at < now - narrow)
                    & or_(SandboxHome.session_id.is_(None), ~session_active),
                ),
            )
            .order_by(SandboxHome.active_at)
        )
        candidates = list(rows.all())
        await self._session.commit()
        destroyed = 0
        for home in candidates:
            if destroyed >= DESTROYS_PER_SWEEP:
                break
            if not self._hub.is_online(home.device_id):
                continue
            current = home.session_id is not None
            holder_id = home.topic_id if current else None
            if home.stopped_at is not None:
                # An unfinished removal: done again, whatever it is now.
                if await self._destroy(
                    home.id,
                    holder_id,
                    home.device_id,
                    max(now - home.active_at, timedelta()),
                    idle_for=_idle_for(home.spec),
                ):
                    destroyed += 1
                continue
            if home.frozen_at is not None:
                # Frozen: it is not pinged and no turn can be running on it.
                # Past its keep time it is destroyed; before that it waits for
                # its session or for a slot to be needed elsewhere.
                if now - home.frozen_at < _frozen_keep():
                    continue
                if await self._destroy(
                    home.id,
                    holder_id,
                    home.device_id,
                    now - home.frozen_at,
                    idle_for=_idle_for(home.spec),
                    frozen=True,
                ):
                    destroyed += 1
                continue
            held = self._held_for(home, pressed)
            since = await self._idle_since(home, now, held)
            if since is None:
                continue
            idle = now - since
            if (
                settings.cloud_sandbox_freeze
                and home.host_id is not None
                and await self._freeze(home, holder_id, held)
            ):
                # Kept on its host: its slot and its home stay its session's.
                continue
            # A home its session still uses is destroyed under its holder's
            # lock.
            if await self._destroy(
                home.id,
                holder_id,
                home.device_id,
                idle,
                idle_for=held,
            ):
                destroyed += 1
        return destroyed

    async def _pressed(self) -> bool:
        """Whether the pool has no place for another sandbox right now: a
        session is waiting on a shared host's slot, or no host that may take
        one has a free slot. A wait on a person's computer or on a whole VM
        presses nothing: neither takes one of the shared slots."""
        waiting = await self._session.scalar(
            select(SandboxHome.id)
            .join(CloudHost, CloudHost.id == SandboxHome.host_id)
            .where(
                # A session waiting on a shared host's slot. One waiting on a
                # person's computer takes no pool slot, and one waiting on a
                # whole VM it has to itself presses nothing either: freeing a
                # shared slot does not bring that machine up.
                CloudHost.whole_machine.is_(False),
                SandboxHome.waiting_since.is_not(None),
                SandboxHome.stopped_at.is_(None),
            )
            .limit(1)
        )
        if waiting is not None:
            await self._session.commit()
            return True
        hosts = await self._repo.live()
        load = await self._repo.occupancy()
        await self._session.commit()
        return not any(free_slots(host, load) for host in hosts if accepting(host))

    @staticmethod
    def _held_for(home, pressed: bool) -> timedelta:
        """How long that home is kept before it is idle: a cloud sandbox on a
        pressed pool goes at the shorter time, everything else at its spec's."""
        idle_for = _idle_for(home.spec)
        if pressed and home.host_id is not None:
            return min(idle_for, _pressed_idle())
        return idle_for

    async def at_work(self, conversation_id, agent_handle, now: datetime) -> bool:
        """Whether a turn of this session's own is running: one in the
        conversation it works in, under its own seat (``_mine``), and not a row
        left open past ``STALE_TURN`` — a corpse must not hold a machine for
        good either. Asked of a whole cloud VM's session before its VM is
        checkpointed or released (``cloud_vm``)."""
        active = (
            await self._session.scalar(
                select(AgentTurn.id)
                .where(
                    _mine(conversation_id, agent_handle),
                    AgentTurn.stopped_at.is_(None),
                    AgentTurn.started_at > now - STALE_TURN,
                )
                .limit(1)
            )
        ) is not None
        return active or (
            self._recovering is not None and self._recovering(conversation_id)
        )

    async def _session_recovering(self, session_id: uuid.UUID | None) -> bool:
        if session_id is None or self._recovering is None:
            return False
        conversation = await self._session.scalar(
            select(AgentSession.conversation_id).where(AgentSession.id == session_id)
        )
        return conversation is not None and self._recovering(conversation)

    async def _idle_since(
        self, home, now: datetime, idle_for: timedelta
    ) -> datetime | None:
        """Since when the session has been idle for at least ``idle_for``, or
        None while it is not."""

        if home.session_id is None:
            return home.active_at
        if self._recovering is not None:
            recovering = await self._session_recovering(home.session_id)
            await self._session.commit()
            if recovering:
                return None
        open_turns, last_end = await self._turns_of(home.session_id, now)
        await self._session.commit()
        if open_turns:
            return None
        since = max(home.active_at, last_end or home.active_at)
        if now - since < idle_for:
            return None
        cap = _background_cap(home.spec)
        if now - since < cap and await self._waiting_on_checks(home):
            # The line has handed its delivery over and is waiting on the
            # checks the platform is watching for its card. The poller wakes
            # its session when they finish; the sandbox it comes back to is
            # this one instead of a rebuilt one and the documents read again.
            # Held on the background command's clock — both are waits on
            # something outside the machine — so a card nobody looks at again
            # cannot keep a host for a day.
            return None
        lease = await self._session.scalar(
            select(AgentSession.work_lease).where(AgentSession.id == home.session_id)
        )
        await self._session.commit()
        until = (lease or {}).get("claim_until")
        if until and datetime.fromisoformat(until) > now:
            # A tool call is starting it right now.
            return None
        if lease and lease.get("state") and lease.get("device_id") == home.device_id:
            asked = _asked.get(home.id)
            if asked is not None and now - asked[0] < RECHECK:
                return None
            asked_at, was_running = asked if asked is not None else (None, False)
            try:
                info = await execution.call(
                    lease, "ping", {}, hub=self._hub, timeout=30
                )
                running = bool(info.get("running_commands"))
            except (RuntimeError, TimeoutError):
                # The machine did not answer: what it had running is unknown,
                # and a home that cannot be asked is not held for it. A
                # command that was running at the last ask still counts as
                # one that just ended, so a single failed ping does not take
                # the sandbox out from under the session.
                info, running = {}, False
            _asked[home.id] = (now, running)
            if running:
                if now - since < cap:
                    return None
                # A turn may have started while the machine was asked (the
                # ping is a round trip): a session at work keeps even a command
                # it just started.
                open_turns, _ = await self._turns_of(home.session_id, datetime.now(UTC))
                await self._session.commit()
                if open_turns:
                    return None
                commands = [str(name) for name in info.get("running_command_ids") or ()]
                if commands and await self._stop_commands(lease, commands):
                    # Past the cap the command is stopped, not the sandbox:
                    # the session wakes to read its result in the home it left,
                    # and that stop is activity that starts a fresh window.
                    await self._wake(home.id, now)
                    return None
                # A machine that cannot name what it runs (an executor older
                # than that field) keeps the cap's old meaning.
                return since
            if was_running and asked_at is not None and now - asked_at <= WOKEN_FOR:
                # The command ended since the last ask. Its end is the session
                # waking to read it — activity, not the silence the idle clock
                # measures — so the wake starts a fresh idle window rather
                # than the sweep taking the home in the seconds before its
                # next tool call.
                await self._wake(home.id, now)
                return None
        _asked.pop(home.id, None)
        return since

    async def _turns_of(self, session_id, now: datetime) -> tuple[int, datetime | None]:
        """How many turns of the session are open, and when its last one
        stopped: the two things that say whether it is at work."""
        turns = (
            await self._session.execute(
                select(
                    # An interval left open past the orphan sweep's limit is a
                    # corpse, and must not read as this session still at work.
                    func.count().filter(
                        AgentTurn.stopped_at.is_(None),
                        AgentTurn.started_at > now - STALE_TURN,
                    ),
                    func.max(AgentTurn.stopped_at),
                ).where(_own_turns(session_id))
            )
        ).one()
        return int(turns[0]), turns[1]

    async def _wake(self, home_id: uuid.UUID, now: datetime) -> None:
        """Move a home's idle clock to now: the session is waking to work in
        it, and idleness is measured from the wake."""
        await self._session.execute(
            update(SandboxHome).where(SandboxHome.id == home_id).values(active_at=now)
        )
        await self._session.commit()

    async def _stop_commands(self, lease: dict, commands: list[str]) -> bool:
        """Stop the commands a session left running past its background cap.

        The cap is what keeps a hung command from holding a host for good; the
        sandbox is not part of that bargain. Stopping the command leaves the
        session's home, the work in it, and the place its next tool call lands.
        """
        for command in commands:
            try:
                await execution.call(
                    lease,
                    "control",
                    {
                        "subtype": "shell",
                        "operation": "signal",
                        "command_id": command,
                        "signal": TERM,
                    },
                    hub=self._hub,
                    timeout=30,
                )
            except (RuntimeError, TimeoutError):
                logger.warning(
                    "stopping command %s of an idle sandbox failed",
                    command,
                    exc_info=True,
                )
                return False
        return True

    async def _waiting_on_checks(self, home) -> bool:
        """Whether the task this home's session works in is waiting on the
        checks the platform is watching for its card.

        The conversation first, then the card: which conversation a session
        works in is this layer's question (``agent_session``, and a task of a
        room works in its own), and which card says CI is running is
        ``review``'s — asked of it by task id rather than by joining across.
        """
        conversation = await self._session.scalar(
            select(AgentSession.conversation_id).where(
                AgentSession.id == home.session_id
            )
        )
        await self._session.commit()
        if conversation is None:
            return False
        answer = await waiting_on_checks(self._session, conversation)
        # The read above opened a transaction; the host call or the destroy
        # that follows must not hold it.
        await self._session.commit()
        return answer

    async def _freeze(self, home, holder_id, held: timedelta) -> bool:
        """Keep an idle sandbox on its host rather than destroying it.

        Taken like a destroy — the holder's lock, the home's, the look again —
        because a tool call arriving meanwhile must either see it frozen (and
        wake it) or move its idle clock first. The session's one checkpoint
        runs between the two lockings, with no lock held; a checkpoint that
        fails does not keep the sandbox awake. Answers whether the sandbox is
        frozen now; False leaves the caller its old destroy.
        """
        home_id = home.id
        device_id = home.device_id
        if holder_id is not None:
            await holders.lock(self._session, holder_id)
        row = await self._repo.lock_home(home_id)
        now = datetime.now(UTC)
        if row is None or row.frozen_at is not None or row.stopped_at is not None:
            await self._session.commit()
            return False
        if now - row.active_at < held:
            await self._session.commit()
            return False
        lease: dict = {}
        start = None
        if row.session_id is not None:
            session_row = await self._session.scalar(
                select(AgentSession)
                .where(AgentSession.id == row.session_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if session_row is not None:
                lease = session_row.work_lease or {}
                until = lease.get("claim_until")
                if until and datetime.fromisoformat(until) > now:
                    # A tool call is starting it right now.
                    await self._session.commit()
                    return False
                open_turns, last_end = await self._turns_of(row.session_id, now)
                if open_turns or (last_end is not None and now - last_end < held):
                    await self._session.commit()
                    return False
                if await self._session_recovering(row.session_id):
                    await self._session.commit()
                    return False
                if lease.get("status", "ready") == "ready" and lease.get("state"):
                    # The same checkpoint a turn's Stop runs, so a freeze
                    # loses no more than a destroy would.
                    try:
                        from app.domain.machine import session_work

                        start = await session_work.restart_executor(
                            self._session, session_row, lease
                        )
                    except Exception:
                        logger.warning(
                            "no checkpoint for session %s before freezing",
                            row.session_id,
                            exc_info=True,
                        )
        active_seen = row.active_at
        await self._session.commit()
        if start is not None:
            await _checkpoint(self._hub, lease, start)
        if holder_id is not None:
            await holders.lock(self._session, holder_id)
        row = await self._repo.lock_home(home_id)
        now = datetime.now(UTC)
        if row is None or row.frozen_at is not None or row.stopped_at is not None:
            await self._session.commit()
            return False
        if row.active_at > active_seen or now - row.active_at < held:
            # A tool call came in while the checkpoint ran (it moves the idle
            # clock): the sandbox is not idle any more.
            await self._session.commit()
            return False
        if row.session_id is not None:
            open_turns, last_end = await self._turns_of(row.session_id, now)
            if open_turns or (last_end is not None and now - last_end < held):
                await self._session.commit()
                return False
        resource = row.resource_id
        marked = now
        row.frozen_at = marked
        await self._session.commit()
        # The clock stops here: the run is charged to when the sandbox stopped
        # running, and the meter's own sweep would otherwise close it a little
        # late (`metering.observe`).
        await metering.stop_billing(self._session, home_id)
        try:
            await self._freeze_on_host(device_id, resource)
        except (RuntimeError, TimeoutError, SandboxHomeError):
            # The host cannot stop it: fall back to the old lifecycle rather
            # than call a running sandbox frozen. It may have stopped before
            # failing (the freeze is one command, its swap and reclaim after),
            # so it is thawed before it is called running again.
            logger.warning("freezing sandbox %s failed", home_id, exc_info=True)
            try:
                await run_on_host(
                    self._hub, device_id, "thaw", timeout=30, names=[resource]
                )
            except (RuntimeError, TimeoutError):
                logger.warning(
                    "sandbox %s may have stayed frozen", home_id, exc_info=True
                )
            row = await self._repo.lock_home(home_id)
            if row is not None and row.frozen_at == marked:
                row.frozen_at = None
            await self._session.commit()
            return False
        # A tool call, or a destroy, may have asked for it back while its
        # processes were being stopped: never leave it frozen then.
        if holder_id is not None:
            await holders.lock(self._session, holder_id)
        row = await self._repo.lock_home(home_id)
        woke = (
            row is None
            or row.frozen_at != marked
            or row.stopped_at is not None
            or row.active_at > active_seen
        )
        await self._session.commit()
        if woke:
            try:
                await thaw_on_host(self._hub, device_id, resource)
            except (RuntimeError, TimeoutError, SandboxHomeError):
                logger.warning(
                    "sandbox %s asked to wake while freezing stayed frozen",
                    home_id,
                    exc_info=True,
                )
        return not woke

    async def _freeze_on_host(self, device_id: str, resource: str) -> None:
        """Stop a sandbox's processes, let it swap, and have it give memory
        back (``sandbox_home``'s ``freeze``, ``swap`` and ``reclaim``).

        The freeze must go through — a sandbox that is not stopped cannot be
        kept unbilled — so its failure is raised. Swap and reclaim only give
        the host memory back: a host with no swap file freezes just the same
        without the first."""
        answer = await run_on_host(
            self._hub, device_id, "freeze", timeout=30, names=[resource]
        )
        if answer.get("freeze_error"):
            raise SandboxHomeError(str(answer["freeze_error"]))
        try:
            swapped = await run_on_host(
                self._hub,
                device_id,
                "swap",
                timeout=30,
                name=resource,
                mib=settings.cloud_sandbox_memory_mb,
            )
            if swapped.get("swap_error"):
                logger.warning(
                    "letting sandbox %s swap failed: %s",
                    resource,
                    swapped["swap_error"],
                )
        except (RuntimeError, TimeoutError):
            logger.warning("letting sandbox %s swap failed", resource)
        try:
            given = await run_on_host(
                self._hub, device_id, "reclaim", timeout=60, names=[resource]
            )
            if given.get("reclaim_error"):
                logger.warning(
                    "having sandbox %s give memory back failed: %s",
                    resource,
                    given["reclaim_error"],
                )
        except (RuntimeError, TimeoutError):
            logger.warning("having sandbox %s give memory back failed", resource)

    async def destroy_frozen_on(
        self,
        device_id: str,
        *,
        host_id: uuid.UUID | None = None,
        limit: int = DESTROYS_PER_SWEEP,
    ) -> int:
        """Destroy the frozen sandboxes on one machine, oldest first: called
        by the disk guard on a host short of room and by the reaper on a host
        being rebuilt (``disk_guard``, ``reaper``). No holder is taken — these
        run in their own sweep — and one thawed or in use meanwhile is left to
        the idle sweep."""
        now = datetime.now(UTC)
        device = func.coalesce(CloudHost.device_id, SandboxHome.device_id)
        query = (
            select(SandboxHome)
            .outerjoin(CloudHost, CloudHost.id == SandboxHome.host_id)
            .where(
                device == device_id,
                SandboxHome.frozen_at.is_not(None),
                SandboxHome.stopped_at.is_(None),
            )
        )
        if host_id is not None:
            query = query.where(SandboxHome.host_id == host_id)
        rows = list(await self._session.scalars(query.order_by(SandboxHome.frozen_at)))
        await self._session.commit()
        if not self._hub.is_online(device_id):
            return 0
        rows.sort(key=frozen_eviction_key)
        destroyed = 0
        for home in rows[:limit]:
            if await self._destroy(
                home.id,
                None,
                device_id,
                now - home.frozen_at,
                idle_for=_idle_for(home.spec),
                frozen=True,
            ):
                destroyed += 1
        return destroyed

    async def evict_one_frozen(self) -> bool:
        """Destroy the frozen sandbox frozen longest ago on an accepting host,
        when no accepting host has a free slot: the pool calls this before
        opening another host (``HostPool.place``). Frozen sandboxes still
        count in the occupancy; the one gone frees its slot for the session
        waiting. False when nothing was evicted and the old path stands."""
        load = await self._repo.occupancy()
        hosts = [
            host
            for host in await self._repo.live()
            if accepting(host)
            and host.device_id is not None
            and self._hub.is_online(host.device_id)
        ]
        await self._session.commit()
        if not hosts or any(free_slots(host, load) for host in hosts):
            return False
        by_id = {host.id: host for host in hosts}
        rows = list(
            await self._session.scalars(
                select(SandboxHome).where(
                    SandboxHome.host_id.in_(list(by_id)),
                    SandboxHome.frozen_at.is_not(None),
                    SandboxHome.stopped_at.is_(None),
                )
            )
        )
        await self._session.commit()
        if not rows:
            return False
        home = min(rows, key=frozen_eviction_key)
        host = by_id[home.host_id]
        assert host.device_id is not None
        return await self._destroy(
            home.id,
            None,
            host.device_id,
            datetime.now(UTC) - home.frozen_at,
            idle_for=_idle_for(home.spec),
            frozen=True,
        )

    async def _destroy(
        self,
        home_id: uuid.UUID,
        holder_id: uuid.UUID | None,
        device_id: str,
        idle: timedelta | None,
        *,
        idle_for: timedelta | None = None,
        frozen: bool = False,
    ) -> bool:
        """Destroy one sandbox: idle for ``idle``, or, with ``idle`` None,
        because its project's credits ran out (``metering.unpaid_sandboxes``)
        or an earlier removal did not finish.

        ``idle_for`` is how long this home was to be kept before it counts as
        idle — the shorter pressed time for a cloud sandbox (``_held_for``);
        without it, its spec's. ``frozen`` says the caller picked it while it
        was frozen (past its keep time, or to free a slot): it is not in use,
        so the idle and turn checks are skipped — but if it woke before this
        lock was taken, it is left to the idle sweep instead."""
        if holder_id is not None:
            # The holder's lock first, as a tool call takes it: one that arrives
            # now waits until the home is marked, and then waits for it to go.
            # An archived room takes no tool call, but its sandbox still goes:
            # one left running holds its host's slot, and its host, for good.
            await holders.lock(self._session, holder_id)
        home = await self._repo.lock_home(home_id)
        now = datetime.now(UTC)
        if home is None:
            await self._session.commit()
            return False
        held = idle_for if idle_for is not None else _idle_for(home.spec)
        was_frozen = home.frozen_at is not None
        if frozen and home.stopped_at is None and not was_frozen:
            # A tool call woke it between the look and this lock; it is in use
            # again, and the idle sweep decides on it as any running sandbox.
            await self._session.commit()
            return False
        if (
            home.stopped_at is None
            and idle is not None
            and not was_frozen
            and now - home.active_at < held
        ):
            await self._session.commit()
            return False
        if (
            home.stopped_at is None
            and idle is not None
            and not was_frozen
            and home.session_id is not None
        ):
            # The home was picked from a look taken before this lock. A turn
            # may have started since — the sweep that picked it ran a second
            # before the session woke to a background command's result — and a
            # session at work keeps its sandbox however long it was idle when
            # the look was taken.
            open_turns, last_end = await self._turns_of(home.session_id, now)
            if open_turns or (last_end is not None and now - last_end < held):
                await self._session.commit()
                return False
        if home.stopped_at is not None and now - home.stopped_at < STOP_HOLD:
            # Another sweep is removing it.
            await self._session.commit()
            return False
        if (
            idle is not None
            and not was_frozen
            and await self._session_recovering(home.session_id)
        ):
            await self._session.commit()
            return False
        unpaid = home.stopped_at is None and idle is None
        home.stopped_at = now
        project, resource = str(holders.namespace_of(home)), home.resource_id
        await self._session.commit()
        try:
            await run_on_host(
                self._hub,
                device_id,
                "destroy",
                timeout=STOP_HOLD.total_seconds() - 30,
                project=project,
                resource=resource,
            )
        except (RuntimeError, TimeoutError):
            # Marked, it takes no tool call; a later sweep tries again.
            logger.warning("destroying sandbox %s failed", home_id, exc_info=True)
            return False
        if holder_id is not None:
            await holders.lock(self._session, holder_id)
        home = await self._repo.lock_home(home_id)
        if home is None:
            await self._session.commit()
            return False
        line = None
        topic_id = home.topic_id
        if home.session_id is not None:
            # Its next tool call lands in a new sandbox, and is told so.
            row = await self._session.scalar(
                select(AgentSession)
                .where(AgentSession.id == home.session_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if row is not None:
                row.execution_request = {
                    **(row.execution_request or {}),
                    LOST_KEY: True,
                }
                # The platform has just taken this session's machine away, and
                # it is the one authority that knows a runner will never report
                # the end of the turn that was running on it. Recording that
                # here — where the destruction happens — is what lets the room's
                # orphan sweep collect the interval instead of leaving it open
                # for good: without it, `death_evidence.unknown_row` reads the
                # turn as "nobody has said it died" and holds it, correctly,
                # open (FB-56 legacy③). This is not "unreachable is dead": a
                # machine merely out of reach is still unknown, and is never
                # touched by this.
                await turn_inputs.close_turns_of_a_destroyed_machine(
                    self._session,
                    conversation_id=row.conversation_id,
                    agent_handle=row.agent_handle,
                    harness=row.harness,
                    native_session_id=row.resume_token,
                    at=now,
                )
            line = (
                await tell_unpaid(self._session, home)
                if unpaid
                else await tell_released(
                    self._session,
                    home,
                    max(1, int((idle or timedelta()).total_seconds() // 60)),
                )
            )
        await self._repo.delete_home(home)
        await self._session.commit()
        await publish_line(topic_id, line)
        logger.info(
            "sandbox %s destroyed %s",
            home_id,
            "with its credits spent" if unpaid else f"after {idle} idle",
        )
        return True
