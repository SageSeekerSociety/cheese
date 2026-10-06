"""Cloud sandboxes sleep when idle, and long-asleep homes leave their host.

A sandbox is **idle** when its room runs no turn and its session has asked for
no tool for ``cloud_sandbox_idle_stop_s`` — counted from the later of its last
tool call (``CloudHostHome.active_at``) and the end of the room's last turn.
Turns are recorded per room, so any turn in the room keeps every sandbox of it
awake. A command the executor is still running for the session — a Bash
call sent to the background, a long build or test whose result the next turn
reads (``running_commands``) — keeps an idle sandbox up too, but only until
``cloud_sandbox_background_cap_s`` after that activity. A process the agent
detached itself (``nohup … &``, a dev server) is not work in progress and keeps
nothing up. A home its session left is idle by definition and is measured by
its own activity alone. A sandbox whose project has run out of credits is
stopped as soon as its room runs no turn (``metering``), idle or not.

**Asleep**, a sandbox's executor and everything still running in its home are
stopped (``sandbox_home.sleep``). The home stays on the host's disk and holds
no slot (``services.free_slots``). The session's next tool call starts it again
on that host, through the same install path that starts any executor.

**Archived**, a home is written to the private bucket and deleted from its
host. A sleeping home is archived after ``cloud_sandbox_archive_after_s``; as
soon as its host is draining (idle for ``cloud_host_idle_hold_s``); and first
of all when its session wants it back on a host that has no slot to wake it in
(``SandboxMustMove``). The archive
is verified before the home is deleted: the bucket must report the size and
MD5 the host wrote. The session's next tool call places it on any host and
restores it there before the executor starts. Code is truth in git — every
turn's Stop checkpoint has already run ``cheese sync --all`` — and the archive
is a cache of the rest: what was not committed, the environment, the build.

The bytes never pass through the backend: the host PUTs and GETs the object
through a URL signed for that one object, for an hour.

A session's whole cloud VM is neither put to sleep nor archived: idle by the
same measure for ``cloud_vm_idle_release_s``, it is pushed and released
(``cloud_vm``), on the same clock (``runner.SandboxSweeper``).
"""

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.storage import private_storage
from app.domain.agent import execution, resource_cleanup
from app.domain.agent.device_hub import device_hub
from app.domain.agent_session.models import AgentSession
from app.domain.machine import sandbox_home
from app.domain.machine.models import CloudHost, CloudHostHome
from app.domain.machine.progress import (
    publish_line,
    tell_archive_lost,
    tell_asleep,
    tell_unpaid,
)
from app.domain.machine.repositories import CloudHostRepository

logger = logging.getLogger("cheese.machine.lifecycle")


class SandboxBusy(Exception):
    """The session's home is being stopped, archived or restored by someone
    else; the tool call waits for that to finish."""


#: How long a stop, an archive or a restore may hold a home before another
#: sweep may take it over: past the longest the host is given for the step.
STOP_HOLD = timedelta(minutes=3)
ARCHIVE_HOLD = timedelta(minutes=25)
#: How long the URL a host moves an archive with is good for.
URL_TTL_S = 3600
#: A sandbox whose background command keeps it up is asked again this often.
RECHECK = timedelta(minutes=1)
#: An archive that failed — a file the host cannot read, a home over the
#: archive limit — is tried again after this long, not
#: on every sweep; the home stays where it is meanwhile.
ARCHIVE_RETRY = timedelta(hours=6)
#: Stops and archives made per sweep: stopping is quick, archiving is not.
STOPS_PER_SWEEP = 20
ARCHIVES_PER_SWEEP = 3

# When each home kept up by its background work was last asked, in this
# process: the sweep runs every few seconds, and asking is a call to the host.
_asked: dict[uuid.UUID, datetime] = {}


class SandboxHomeError(RuntimeError):
    """The host could not do what was asked to the home."""


def archives_configured() -> bool:
    """Whether there is a private bucket to archive homes to."""
    try:
        private_storage()
    except RuntimeError:
        return False
    return True


async def delete_archive(key: str | None, *, missing_ok: bool = True) -> None:
    """Delete an archive the platform no longer needs. With ``missing_ok`` a
    bucket that cannot be reached only leaves an orphan object behind."""
    if key is None:
        return
    try:
        await private_storage().delete(key)
    except Exception:
        if not missing_ok:
            raise
        logger.warning("deleting sandbox archive %s failed", key, exc_info=True)


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


class SandboxLifecycle:
    def __init__(self, session: AsyncSession, hub=None, storage=None) -> None:
        self._session = session
        self._repo = CloudHostRepository(session)
        self._hub = hub or device_hub
        self._storage = storage

    def _bucket(self):
        return self._storage or private_storage()

    async def sweep(self) -> dict[str, int]:
        from app.domain.machine import metering

        asleep = await self.stop_idle()
        for home in await metering.unpaid_sandboxes(self._session):
            if self._hub.is_online(home.device_id) and await self._stop(
                home.id,
                home.topic_id
                if home.session_id is not None and home.left_at is None
                else None,
                home.device_id,
                None,
            ):
                asleep += 1
        archived = await self.archive_due() if archives_configured() else 0
        return {"asleep": asleep, "archived": archived}

    # --- sleep ---------------------------------------------------------------

    async def stop_idle(self) -> int:
        """Stop every idle sandbox; returns how many were stopped."""
        now = datetime.now(UTC)
        idle_for = timedelta(seconds=settings.cloud_sandbox_idle_stop_s)
        from app.domain.agent.models import AgentTurn

        # A turn running in the conversation the home's session works in, or
        # one that ended within the idle time, keeps that sandbox awake; a
        # home its session left is measured by its own activity alone. Not the
        # room's turns: every task of a channel hangs under it, and one task at
        # work kept the sandboxes of all the others up for days. Decided here,
        # not after a limit: busy homes would otherwise take every place in the
        # batch.
        session_active = (
            select(AgentTurn.id)
            .where(
                AgentTurn.conversation_id
                == select(AgentSession.conversation_id)
                .where(AgentSession.id == CloudHostHome.session_id)
                .correlate(CloudHostHome)
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
                CloudHostHome.id,
                CloudHostHome.session_id,
                CloudHostHome.topic_id,
                CloudHostHome.left_at,
                CloudHostHome.active_at,
                CloudHost.device_id,
            )
            .join(CloudHost, CloudHost.id == CloudHostHome.host_id)
            .where(
                CloudHostHome.stopped_at.is_(None),
                or_(
                    CloudHostHome.busy_until.is_(None),
                    CloudHostHome.busy_until < now,
                ),
                CloudHostHome.active_at < now - idle_for,
                CloudHost.released_at.is_(None),
                CloudHost.device_id.is_not(None),
                # A session's whole cloud VM is released, not put to sleep
                # (``cloud_vm``).
                CloudHost.whole_machine.is_(False),
                or_(
                    CloudHostHome.left_at.is_not(None),
                    CloudHostHome.session_id.is_(None),
                    ~session_active,
                ),
            )
            .order_by(CloudHostHome.active_at)
        )
        candidates = list(rows.all())
        await self._session.commit()
        stopped = 0
        for home in candidates:
            if stopped >= STOPS_PER_SWEEP:
                break
            if not self._hub.is_online(home.device_id):
                continue
            since = await self._idle_since(home, now, idle_for)
            if since is None:
                continue
            # A home its session still uses is stopped under its room's lock.
            current = home.session_id is not None and home.left_at is None
            if await self._stop(
                home.id,
                home.topic_id if current else None,
                home.device_id,
                now - since,
            ):
                stopped += 1
        return stopped

    async def _idle_since(
        self, home, now: datetime, idle_for: timedelta
    ) -> datetime | None:
        """Since when the session has been idle for at least ``idle_for``, or
        None while it is not."""
        from app.domain.agent.models import AgentTurn

        if home.left_at is not None or home.session_id is None:
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
        lease = await self._session.scalar(
            select(AgentSession.work_lease).where(AgentSession.id == home.session_id)
        )
        await self._session.commit()
        until = (lease or {}).get("claim_until")
        if until and datetime.fromisoformat(until) > now:
            # A tool call is starting it right now.
            return None
        cap = timedelta(seconds=settings.cloud_sandbox_background_cap_s)
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

    async def _stop(
        self,
        home_id: uuid.UUID,
        room_id: uuid.UUID | None,
        device_id: str,
        idle: timedelta | None,
    ) -> bool:
        """Stop one sandbox: idle for ``idle``, or, with ``idle`` None, because
        its project's credits ran out (``metering.unpaid_sandboxes``)."""
        from app.domain.topic.services import TopicService

        if room_id is not None:
            # The room's lock first, as a tool call takes it: one that arrives
            # now waits until this stop is recorded, and then wakes it. An
            # archived room takes no tool call, but its sandbox still sleeps:
            # one left running holds its host's slot, and its host, for good.
            await TopicService(self._session).lock(room_id)
        home = await self._repo.lock_home(home_id)
        now = datetime.now(UTC)
        if (
            home is None
            or home.stopped_at is not None
            or (home.busy_until is not None and home.busy_until > now)
            or (
                idle is not None
                and now - home.active_at
                < timedelta(seconds=settings.cloud_sandbox_idle_stop_s)
            )
        ):
            await self._session.commit()
            return False
        home.busy_until = now + STOP_HOLD
        project, resource = str(home.project_id), home.resource_id
        await self._session.commit()
        try:
            await run_on_host(
                self._hub,
                device_id,
                "sleep",
                timeout=STOP_HOLD.total_seconds() - 30,
                project=project,
                resource=resource,
            )
        except (RuntimeError, TimeoutError):
            logger.warning("stopping sandbox %s failed", home_id, exc_info=True)
            home = await self._repo.lock_home(home_id)
            if home is not None:
                home.busy_until = None
            await self._session.commit()
            return False
        home = await self._repo.lock_home(home_id)
        line = None
        if home is not None:
            topic_id = home.topic_id
            home.busy_until = None
            home.stopped_at = datetime.now(UTC)
            if home.session_id is not None and home.left_at is None:
                line = (
                    await tell_unpaid(self._session, home)
                    if idle is None
                    else await tell_asleep(
                        self._session, home, max(1, int(idle.total_seconds() // 60))
                    )
                )
            await self._session.commit()
            await publish_line(topic_id, line)
        else:
            await self._session.commit()
        logger.info(
            "cloud sandbox %s asleep %s",
            home_id,
            "with its credits spent" if idle is None else f"after {idle} idle",
        )
        return home is not None

    # --- archive -------------------------------------------------------------

    async def archive_due(self) -> int:
        """Archive the sleeping homes that are due: asleep past
        ``cloud_sandbox_archive_after_s``, or on a draining host."""
        now = datetime.now(UTC)
        after = timedelta(seconds=settings.cloud_sandbox_archive_after_s)
        rows = await self._session.execute(
            select(CloudHostHome.id, CloudHost.device_id)
            .join(CloudHost, CloudHost.id == CloudHostHome.host_id)
            .where(
                CloudHostHome.stopped_at.is_not(None),
                or_(
                    CloudHostHome.busy_until.is_(None),
                    CloudHostHome.busy_until < now,
                ),
                or_(
                    CloudHostHome.archive_failed_at.is_(None),
                    CloudHostHome.archive_failed_at < now - ARCHIVE_RETRY,
                ),
                CloudHost.released_at.is_(None),
                CloudHost.device_id.is_not(None),
                CloudHost.whole_machine.is_(False),
                or_(
                    # A session waits for it to wake where there is a slot.
                    CloudHostHome.waiting_since.is_not(None),
                    CloudHost.draining,
                    CloudHostHome.active_at < now - after,
                ),
            )
            .order_by(
                CloudHostHome.waiting_since.desc().nulls_last(),
                CloudHostHome.active_at,
            )
        )
        due = list(rows.all())
        await self._session.commit()
        archived = tried = 0
        for home_id, device_id in due:
            if tried >= ARCHIVES_PER_SWEEP:
                break
            if not self._hub.is_online(device_id):
                continue
            tried += 1
            try:
                if await self.archive(home_id):
                    archived += 1
            except Exception:
                logger.warning(
                    "archiving sandbox home %s failed", home_id, exc_info=True
                )
        return archived

    async def archive(self, home_id: uuid.UUID) -> bool:
        """Archive one sleeping home and delete it from its host.

        The home is deleted only once the bucket reports the size and MD5 the
        host wrote. Its row says it is archived before the host is told to
        delete it: a backend that dies in between leaves a stale copy on the
        host, never a home with nothing behind it.
        """
        now = datetime.now(UTC)
        home = await self._repo.lock_home(home_id)
        if (
            home is None
            or home.host_id is None
            or home.stopped_at is None
            or (home.busy_until is not None and home.busy_until > now)
        ):
            await self._session.commit()
            return False
        host = await self._repo.get(home.host_id)
        assert host is not None and host.device_id is not None
        device_id = host.device_id
        home.busy_until = now + ARCHIVE_HOLD
        project, resource = str(home.project_id), home.resource_id
        if home.archive_key is not None:
            # Placed here to be restored, and not restored yet: its work is in
            # the archive it has. Whatever is here is at most half of that.
            home.host_id = None
            await self._session.commit()
            await self._drop(device_id, home_id, project, resource)
            await self._release(home_id)
            return True
        await self._session.commit()

        bucket = self._bucket()
        key = f"sandbox-archives/{project}/{resource}/{uuid.uuid4().hex}.tar.gz"
        try:
            url = await bucket.presign(key, "put_object", URL_TTL_S)
            written = await run_on_host(
                self._hub,
                device_id,
                "archive",
                timeout=ARCHIVE_HOLD.total_seconds() - 60,
                project=project,
                resource=resource,
                url=url,
            )
            if not written.get("absent"):
                stored = await bucket.stat(key)
                if stored != (int(written["size"]), str(written["md5"])):
                    raise SandboxHomeError(
                        f"the bucket holds {stored}, the host wrote "
                        f"{(written['size'], written['md5'])}"
                    )
        except BaseException as exc:
            await self._release(home_id, failed=str(exc) or type(exc).__name__)
            await delete_archive(key)
            raise

        home = await self._repo.lock_home(home_id)
        if written.get("absent"):
            # Its directory is gone from the host, so nothing of it is kept
            # anywhere: the home goes, as when the room's cleanup removes one,
            # and no longer holds its host. Its session, if it comes back,
            # is placed like a new one.
            if home is not None and home.host_id is not None:
                await self._repo.delete_home(home)
            await self._session.commit()
            logger.warning("sandbox home %s was not on its host; let go", home_id)
            return False
        if home is None or home.host_id is None:
            # The room's cleanup took the home meanwhile.
            await self._session.commit()
            await delete_archive(key)
            return False
        home.host_id = None
        home.archive_key = key
        home.archive_size = int(written["size"])
        home.archive_md5 = str(written["md5"])
        home.archive_published = written.get("published") is True
        await self._session.commit()
        await self._drop(device_id, home_id, project, resource)
        await self._release(home_id)
        logger.info(
            "cloud sandbox home %s archived (%s bytes)", home_id, written["size"]
        )
        return True

    async def _drop(
        self, device_id: str, home_id: uuid.UUID, project: str, resource: str
    ) -> None:
        try:
            await run_on_host(
                self._hub,
                device_id,
                "drop",
                timeout=120,
                project=project,
                resource=resource,
            )
        except (RuntimeError, TimeoutError):
            # The home is safe in the bucket. What is left on the host goes
            # with the host, or is replaced if the home comes back to it.
            logger.warning("dropping archived home %s failed", home_id, exc_info=True)

    async def _release(self, home_id: uuid.UUID, *, failed: str | None = None) -> None:
        """Let the home go; ``failed`` says why the archive did not happen,
        which keeps the sweep off it for ``ARCHIVE_RETRY``."""
        home = await self._repo.lock_home(home_id)
        if home is not None:
            home.busy_until = None
            if failed is not None:
                home.archive_failed_at = datetime.now(UTC)
                home.archive_error = failed[-1000:]
            else:
                home.archive_failed_at = home.archive_error = None
        await self._session.commit()

    # --- restore -------------------------------------------------------------

    async def restore(self, home_id: uuid.UUID, device_id: str) -> dict | None:
        """Unpack the home's archive on the host it was placed on, then let
        the archive go. Returns the room line to publish, if any: one saying
        the archive was gone, when it was.

        Holds the home while it runs, for longer than a tool call's claim on
        its session lasts: a second call waits for this restore
        (``SandboxBusy``) instead of starting another over the same directory.
        """
        now = datetime.now(UTC)
        home = await self._repo.lock_home(home_id)
        assert home is not None and home.archive_key is not None
        if home.busy_until is not None and home.busy_until > now:
            await self._session.commit()
            raise SandboxBusy()
        home.busy_until = now + ARCHIVE_HOLD
        key, size, md5 = home.archive_key, home.archive_size, home.archive_md5
        project, resource = str(home.project_id), home.resource_id
        await self._session.commit()
        try:
            url = await self._bucket().presign(key, "get_object", URL_TTL_S)
            answer = await run_on_host(
                self._hub,
                device_id,
                "restore",
                timeout=ARCHIVE_HOLD.total_seconds() - 60,
                project=project,
                resource=resource,
                url=url,
                size=size,
                md5=md5,
            )
        except BaseException:
            await self._release(home_id)
            raise
        home = await self._repo.lock_home(home_id)
        line = None
        if home is not None:
            home.busy_until = None
            home.archive_key = home.archive_size = home.archive_md5 = None
            home.archive_published = None
            if answer.get("missing"):
                logger.warning("sandbox archive %s was gone at restore", key)
                line = await tell_archive_lost(self._session, home)
        await self._session.commit()
        await delete_archive(key)
        return line
