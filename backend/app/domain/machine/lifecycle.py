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
too, but only until the spec's ``background_cap_s`` after that activity. A
process the agent detached itself (``nohup … &``, a dev server) is not work in
progress and keeps nothing up. A line that has handed its delivery over and is
waiting on the checks the platform is watching for its card
(``review.queries.waiting_on_checks``) is not idle either, on the same clock:
the poller wakes its session when they finish, and the sandbox it comes back
to should be this one rather than a new one and the documents read again. A
home of no session (a room's directory from
before session leases) is measured by its own activity alone. A sandbox whose
project has run out
of credits is destroyed as soon as its room runs no turn (``metering``), idle
or not.

**Destroyed**, a sandbox's executor and everything still running in its home
are stopped and the home is deleted from its machine (``sandbox_home.destroy``);
then its row goes, and on a host with it the host's slot. A machine that is
offline is skipped until it is back. Nothing of the sandbox is kept:
what lasts is the conversation, what the session pushed, and the snapshot
each turn's checkpoint uploads. The session's next tool call places it in a
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

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.agent import execution, resource_cleanup
from app.domain.agent.device_hub import device_hub
from app.domain.agent.environment import STANDARD, spec_of
from app.domain.agent.models import AgentTurn
from app.domain.agent_session.models import LOST_KEY, AgentSession
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
#: Sandboxes destroyed per sweep.
DESTROYS_PER_SWEEP = 20

# When each home kept up by its background work was last asked, in this
# process: the sweep runs every few seconds, and asking is a call to the host.
_asked: dict[uuid.UUID, datetime] = {}


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
                SandboxHome.stopped_at,
                SandboxHome.spec,
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
            held = None
            if home.stopped_at is not None:
                # An unfinished removal: done again, whatever it is now.
                idle = now - home.active_at
            else:
                held = self._held_for(home, pressed)
                since = await self._idle_since(home, now, held)
                if since is None:
                    continue
                idle = now - since
            # A home its session still uses is destroyed under its room's lock.
            if await self._destroy(
                home.id,
                home.topic_id if current else None,
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
                ).where(_own_turns(home.session_id))
            )
        ).one()
        await self._session.commit()
        if turns[0]:
            return None
        since = max(home.active_at, turns[1] or home.active_at)
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
        if (
            lease
            and lease.get("state")
            and lease.get("device_id") == home.device_id
            and now - since < cap
        ):
            asked = _asked.get(home.id)
            if asked is not None and now - asked < RECHECK:
                return None
            _asked[home.id] = now
            try:
                info = await execution.call(
                    lease, "ping", {}, hub=self._hub, timeout=30
                )
            except (RuntimeError, TimeoutError):
                info = {}
            if info.get("running_commands"):
                return None
        _asked.pop(home.id, None)
        return since

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

    async def _destroy(
        self,
        home_id: uuid.UUID,
        room_id: uuid.UUID | None,
        device_id: str,
        idle: timedelta | None,
        *,
        idle_for: timedelta | None = None,
    ) -> bool:
        """Destroy one sandbox: idle for ``idle``, or, with ``idle`` None,
        because its project's credits ran out (``metering.unpaid_sandboxes``)
        or an earlier removal did not finish.

        ``idle_for`` is how long this home was to be kept before it counts as
        idle — the shorter pressed time for a cloud sandbox (``_held_for``);
        without it, its spec's."""
        # deferred-import: tests replace this name on app.domain.topic.services
        from app.domain.topic.services import TopicService

        if room_id is not None:
            # The room's lock first, as a tool call takes it: one that arrives
            # now waits until the home is marked, and then waits for it to go.
            # An archived room takes no tool call, but its sandbox still goes:
            # one left running holds its host's slot, and its host, for good.
            await TopicService(self._session).lock(room_id)
        home = await self._repo.lock_home(home_id)
        now = datetime.now(UTC)
        if home is None:
            await self._session.commit()
            return False
        held = idle_for if idle_for is not None else _idle_for(home.spec)
        if home.stopped_at is None and idle is not None and now - home.active_at < held:
            await self._session.commit()
            return False
        if home.stopped_at is not None and now - home.stopped_at < STOP_HOLD:
            # Another sweep is removing it.
            await self._session.commit()
            return False
        if idle is not None and await self._session_recovering(home.session_id):
            await self._session.commit()
            return False
        unpaid = home.stopped_at is None and idle is None
        home.stopped_at = now
        project, resource = str(home.project_id), home.resource_id
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
        if room_id is not None:
            await TopicService(self._session).lock(room_id)
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
