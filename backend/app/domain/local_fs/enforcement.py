"""Getting the grant set onto the machine — and what to do when it is not there.

Two facts make this module exist rather than a call site doing it inline:

* **The platform is not the enforcement point.** The files are on the owner's
  machine, so the grant set has to travel there for anything to be enforced at
  all. The device keeps its own copy and decides again with the disk in front of
  it (``cli/internal/localfs``); what is pushed is not a notification, it is what
  the machine is allowed to do.
* **The owner's computer is not always on.** A push that fails because the machine
  is not connected is not a failure of the grant — the platform is the
  authoritative record either way, and the set is pushed again when the device
  reattaches. A grant that could only be created while the machine happened to be
  online would make 「本机离线时项目照常可用」 false at the first step.

So :func:`push_grants` never raises for an absent machine. It returns an outcome
that says what happened, and :func:`plan_local_access` turns that — plus the
question of whether there is a grant at all — into the one thing a caller needs to
know: can this piece of work reach the local directory right now, and if not, what
does it do instead. The fallback is always the platform workspace, because the
work has to go on somewhere.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.domain.agent.device_hub import DeviceOffline
from app.domain.local_fs.records import DirectoryGrant
from app.domain.local_fs.service import LocalDirectoryService

__all__ = [
    "DeviceLink",
    "LocalAccess",
    "PushOutcome",
    "grant_wire",
    "plan_local_access",
    "push_grants",
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

    @property
    def needs_retry(self) -> bool:
        """Whether the set should be offered again when the machine reattaches.

        True for an absent machine and for one that answered badly. False only
        when the device confirmed the set it now holds — re-pushing an
        acknowledged set on every reconnect would be churn with no effect.
        """
        return not self.delivered


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
    except Exception as exc:  # noqa: BLE001 — a bad answer must not fail the grant
        # The grant is already recorded and is the authoritative record; a device
        # that answered badly has not made the grant wrong, it has only not been
        # told yet. Reported rather than raised for the same reason as offline.
        return PushOutcome(
            delivered=False,
            reason="device_error",
            detail=f"已记录授权，但下发给这台电脑时出错（{exc}），它下次连上来时会重试",
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
