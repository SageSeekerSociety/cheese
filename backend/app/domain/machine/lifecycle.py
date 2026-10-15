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
too, but only until ``cloud_sandbox_background_cap_s`` after that activity. A
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
"""

import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, or_, select
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


class SandboxLifecycle:
    def __init__(self, session: AsyncSession, hub=None) -> None:
        self._session = session
        self._repo = CloudHostRepository(session)
        self._hub = hub or device_hub

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

        # A turn running in the conversation the home's session works in, or
        # one that ended within the idle time, keeps that sandbox up. Not the
        # room's turns: every task of a channel hangs under it, and one task at
        # work kept the sandboxes of all the others up for days. Decided here,
        # not after a limit: busy homes would otherwise take every place in the
        # batch.
        session_active = (
            select(AgentTurn.id)
            .where(
                AgentTurn.conversation_id
                == select(AgentSession.conversation_id)
                .where(AgentSession.id == SandboxHome.session_id)
                .correlate(SandboxHome)
                .scalar_subquery(),
                or_(
                    AgentTurn.stopped_at.is_(None),
                    AgentTurn.stopped_at > now - idle_for,
                ),
            )
            .exists()
        )
        rows = await self._session.execute(
            select(
                SandboxHome.id,
                SandboxHome.session_id,
                SandboxHome.topic_id,
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
                    & (SandboxHome.active_at < now - idle_for)
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
            if home.stopped_at is not None:
                # An unfinished removal: done again, whatever it is now.
                idle = now - home.active_at
            else:
                since = await self._idle_since(home, now, _idle_for(home.spec))
                if since is None:
                    continue
                idle = now - since
            # A home its session still uses is destroyed under its room's lock.
            if await self._destroy(
                home.id, home.topic_id if current else None, home.device_id, idle
            ):
                destroyed += 1
        return destroyed

    async def _idle_since(
        self, home, now: datetime, idle_for: timedelta
    ) -> datetime | None:
        """Since when the session has been idle for at least ``idle_for``, or
        None while it is not."""

        if home.session_id is None:
            return home.active_at
        turns = (
            await self._session.execute(
                select(
                    func.count().filter(AgentTurn.stopped_at.is_(None)),
                    func.max(AgentTurn.stopped_at),
                ).where(
                    AgentTurn.conversation_id
                    == select(AgentSession.conversation_id)
                    .where(AgentSession.id == home.session_id)
                    .scalar_subquery()
                )
            )
        ).one()
        await self._session.commit()
        if turns[0]:
            return None
        since = max(home.active_at, turns[1] or home.active_at)
        if now - since < idle_for:
            return None
        cap = timedelta(seconds=settings.cloud_sandbox_background_cap_s)
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

    async def evict(
        self,
        home_id: uuid.UUID,
        room_id: uuid.UUID | None,
        device_id: str,
        tell: Callable[[AsyncSession, SandboxHome], Awaitable[dict | None]],
    ) -> bool:
        """Destroy one sandbox now, idle or not, and tell its room why with
        ``tell`` (``disk_guard``). Returns whether it was destroyed."""
        return await self._destroy(home_id, room_id, device_id, None, tell=tell)

    async def _destroy(
        self,
        home_id: uuid.UUID,
        room_id: uuid.UUID | None,
        device_id: str,
        idle: timedelta | None,
        *,
        tell: Callable[[AsyncSession, SandboxHome], Awaitable[dict | None]]
        | None = None,
    ) -> bool:
        """Destroy one sandbox: idle for ``idle``, or, with ``idle`` None,
        because its project's credits ran out (``metering.unpaid_sandboxes``),
        an earlier removal did not finish, or it is evicted (``tell``)."""
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
        if home is None or (
            home.stopped_at is None
            and idle is not None
            and now - home.active_at < _idle_for(home.spec)
        ):
            await self._session.commit()
            return False
        if home.stopped_at is not None and now - home.stopped_at < STOP_HOLD:
            # Another sweep is removing it.
            await self._session.commit()
            return False
        unpaid = home.stopped_at is None and idle is None and tell is None
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
                await tell(self._session, home)
                if tell is not None
                else await tell_unpaid(self._session, home)
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
            "evicted"
            if tell is not None
            else "with its credits spent"
            if unpaid
            else f"after {idle} idle",
        )
        return True
