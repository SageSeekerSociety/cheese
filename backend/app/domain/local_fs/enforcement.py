"""Getting the grant set onto the machine — and what to do when it is not there.

Two facts make this module exist rather than a call site doing it inline:

* **The platform is not the enforcement point.** The files are on the owner's
  machine, so the grant set has to travel there for anything to be enforced at
  all. The device keeps its own copy and decides again with the disk in front of
  it (``cli/internal/localfs``); what is pushed is not a notification, it is what
  the machine is allowed to do.
* **The owner's computer is not always on.** A push that fails because the machine
  is not connected is not a failure of the grant — the platform is the
  authoritative record either way, and :func:`push_grants_on_connect` sends the
  machine its current set every time it connects. A grant that could only be
  created while the machine happened to be online would make 「本机离线时项目照常
  可用」 false at the first step.

So :func:`push_grants` never raises for an absent machine. It returns an outcome
that says what happened, and :func:`plan_local_access` turns that — plus the
question of whether there is a grant at all — into the one thing a caller needs to
know: can this piece of work reach the local directory right now, and if not, what
does it do instead. The fallback is always the platform workspace, because the
work has to go on somewhere.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import httpx

from app.domain.agent.device_hub import DeviceCallError, DeviceOffline
from app.domain.local_fs.records import DirectoryGrant
from app.domain.local_fs.service import LocalDirectoryService
from app.domain.local_fs.wiring import sql_local_directory_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

__all__ = [
    "DeviceLink",
    "LocalAccess",
    "PushOutcome",
    "grant_wire",
    "plan_local_access",
    "push_grants",
    "push_grants_on_connect",
]


class DeviceLink(Protocol):
    """The part of ``DeviceHub`` this module needs, so it can be tested without a
    socket. ``DeviceHub`` satisfies it as it stands."""

    def is_online(self, device_id: str) -> bool: ...

    async def push_local_fs_grants(
        self, device_id: str, grants: list[dict[str, Any]], *, timeout: float = ...
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class PushOutcome:
    """What happened when the set was sent to the machine."""

    delivered: bool
    reason: str
    detail: str
    fingerprint: str | None = None


def grant_wire(grant: DirectoryGrant) -> dict[str, Any]:
    """One grant as the device is sent it.

    ``path`` and ``platform`` travel together and that is not redundancy: the
    device normalizes the path itself, and normalizing for the wrong filesystem is
    normalizing with the wrong case rules, which is the wrong containment answer.
    Nothing else about the grant travels — no owner, no token, no handle — because
    the device has no use for any of it and every field on a wire is a field that
    can leak.
    """
    return {
        "id": str(grant.id),
        "path": grant.path,
        "platform": grant.platform.value,
        "mode": grant.mode.value,
        "scope": grant.scope.value,
        "project_id": str(grant.project_id) if grant.project_id else None,
    }


async def push_grants(
    service: LocalDirectoryService,
    link: DeviceLink,
    device_id: str,
) -> PushOutcome:
    """Send this machine the complete live set, and report whether it landed.

    Never raises for an absent or unresponsive machine: both are states the
    caller has to carry on from, and turning either into an exception would make
    authorizing a directory fail whenever the owner's laptop was closed.

    The whole live set goes, both scopes — see
    :meth:`LocalDirectoryService.device_grants` for why filtering by project here
    would silently disable the narrower kind of grant, which is the safer kind.
    """
    async with _one_push_at_a_time(device_id):
        return await _send_current_set(service, link, device_id)


# Per machine: the lock, and how many pushes hold or wait on it. An entry goes
# when its count reaches zero, so the map holds only machines being pushed to.
_push_locks: dict[str, asyncio.Lock] = {}
_push_waiting: dict[str, int] = {}


@asynccontextmanager
async def _one_push_at_a_time(device_id: str) -> AsyncIterator[None]:
    """Read the set and send it, one push per machine at a time.

    Two pushes that overlap, say a reconnect's and a revoke's, each read the
    set and then send it. Unordered, the one that read first can arrive last,
    and the machine keeps the older set: the one that still has the revoked
    directory. Under this lock the push that reads later also sends later.
    Granting and revoking commit before they push, so the later read sees the
    change.

    This orders the pushes made by one process. Two backends pushing to the
    same machine at the same instant, which only a rolling deploy produces, are
    not ordered against each other; the machine's next connection sends it the
    current set either way.
    """
    lock = _push_locks.setdefault(device_id, asyncio.Lock())
    _push_waiting[device_id] = _push_waiting.get(device_id, 0) + 1
    try:
        async with lock:
            yield
    finally:
        _push_waiting[device_id] -= 1
        if not _push_waiting[device_id]:
            del _push_waiting[device_id]
            del _push_locks[device_id]


async def _send_current_set(
    service: LocalDirectoryService, link: DeviceLink, device_id: str
) -> PushOutcome:
    effective = await service.device_grants(device_id)
    payload = [grant_wire(grant) for grant in effective.grants]

    try:
        answer = await link.push_local_fs_grants(device_id, payload)
    except DeviceOffline:
        return PushOutcome(
            delivered=False,
            reason="device_offline",
            detail="这台电脑现在不在线，授权已记录；它下次连上来时会自动生效",
        )
    except (DeviceCallError, TimeoutError) as exc:
        # The grant is already recorded and is the authoritative record; a device
        # that answered badly has not made the grant wrong, it has only not been
        # told yet. Reported rather than raised for the same reason as offline.
        #
        # Only the ways the trip itself fails are caught, here and below.
        # Anything else is a fault in this code, and caught here it read to the
        # owner of the directory as a machine that answered badly: a backend
        # whose hub had no `push_local_fs_grants` at all said that on every grant
        # made on dev.
        return PushOutcome(
            delivered=False,
            reason="device_error",
            detail=f"已记录授权，但下发给这台电脑时出错（{exc}），它下次连上来时会重试",
        )
    except httpx.HTTPError:
        # The connection owner unreachable, or answering with a status of its
        # own (an owner older than this call answers 404). Not the machine's
        # doing, and the error names the owner's internal address, so it goes to
        # the log and the person is told only that the platform did not get it
        # there. The whole set travels on every push, so the machine's next
        # connection carries this one, and so does the next grant or revoke.
        logger.warning(
            "local grants push to %s did not reach the owner",
            device_id,
            exc_info=True,
        )
        return PushOutcome(
            delivered=False,
            reason="platform_error",
            detail="已记录授权，但平台这边没能把它下发给这台电脑；它下次连上来时会重新下发",
        )

    fingerprint = None
    if isinstance(answer, dict):
        fingerprint = answer.get("fingerprint")
    return PushOutcome(
        delivered=True,
        reason="delivered",
        detail="授权已下发到这台电脑，立即生效",
        fingerprint=fingerprint if isinstance(fingerprint, str) else None,
    )


async def push_grants_on_connect(
    session_factory: async_sessionmaker[AsyncSession],
    link: DeviceLink,
    device_id: str,
) -> PushOutcome | None:
    """Send a machine that has just connected the set the platform holds now.

    The machine enforces its own copy, so a grant or a revocation made while it
    was away, or one it refused, is not in force on it until it is sent the
    current set. This is what makes 「它下次连上来时会自动生效」 true. It runs on
    every connection, not only after a push that failed: the platform does not
    know what the machine kept across its own restart, and the machine replaces
    its set whole, so sending the same set again changes nothing.

    A machine that was never granted a directory is not sent anything (None).
    One whose grants were all revoked is sent the empty set.

    A push that does not land is logged and not raised. Nobody waits on this:
    it runs in the background of a connection, and the machine's next
    connection sends the set again.
    """
    async with session_factory() as session:
        service = sql_local_directory_service(session)
        if not await service.ever_granted(device_id):
            return None
        outcome = await push_grants(service, link, device_id)
    if not outcome.delivered:
        logger.warning(
            "local grants not delivered to %s on connect (%s): %s; "
            "sent again on its next connect",
            device_id,
            outcome.reason,
            outcome.detail,
        )
    return outcome


@dataclass(frozen=True, slots=True)
class LocalAccess:
    """Whether this piece of work can reach the local directory right now.

    ``fallback`` is always set when ``available`` is false, and it is always the
    platform workspace: the work has to go on somewhere, and 「等那台电脑开机」
    is not a plan a room can run on. What the person gets instead is a sentence
    that says why.
    """

    available: bool
    reason: str
    detail: str
    fallback: str | None = None


def plan_local_access(
    *, device_online: bool, grants: Sequence[DirectoryGrant]
) -> LocalAccess:
    """Decide where this work runs, from what the platform knows before it asks.

    This is the degradation path, and it is deliberately a pure function: the
    question 「本机离线时项目照常可用吗」 is answerable without a socket, and pinning
    it here means the answer cannot drift as callers are added.

    Three states, and the order matters. No grant at all is not a degradation —
    it is the ordinary case of work that never needed a local directory, and it
    falls back silently because there is nothing to tell the person about. A grant
    with the machine offline *is* a degradation, and it says so: the work moves to
    the platform workspace with a reason, rather than blocking on a laptop.
    """
    live = [g for g in grants if not g.revoked]
    if not live:
        return LocalAccess(
            available=False,
            reason="no_grant",
            detail="这件事没有授权任何本机目录，照常在平台工作区进行",
            fallback="platform_workspace",
        )
    if not device_online:
        return LocalAccess(
            available=False,
            reason="device_offline",
            detail="这台电脑不在线，先按平台工作区进行；它上线后授权目录即可使用",
            fallback="platform_workspace",
        )
    return LocalAccess(
        available=True,
        reason="device_online",
        detail="这台电脑在线，已授权的目录可以使用",
    )
