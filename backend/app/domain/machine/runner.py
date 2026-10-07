"""The cloud host pool's sweep.

Deliberately on its own interval and its own switch. Keeping hosts in line with
MicroCloud, enrolling the ones that came up and sizing the pool is platform
plumbing with no judgment in it, and a host that came up while nobody was
looking must still become a device — so it must not share a switch with
anything a deployment might want off. See ``app.core.background.PeriodicRunner``
for the clock.

Destroying idle sandboxes and releasing idle whole cloud VMs
(``SandboxSweeper``) is the same plumbing on the same switch, in a loop of its
own: a VM's push takes minutes, and a host that came up must not wait that
long to be enrolled.

Metering the sandboxes that run and charging their time
(``ComputeMeterSweeper``) has a loop of its own for the same reason: a
destroyed sandbox is seen within one sweep only if nothing slow is in the way.
"""

import logging

from app.core.db import SessionFactory

logger = logging.getLogger("cheese.machine.runner")


class CloudPoolSweeper:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    async def sweep(self) -> dict[str, int]:
        # Imported here: the machine domain pulls in the device service, and
        # importing it at module scope would drag that into app startup.
        from app.domain.machine.services import HostPool

        async with self._sessions() as session:
            pool = HostPool(session)
            if not pool.available:
                # No MicroCloud credentials — nothing can have been provisioned.
                return {"enrolled": 0, "failed": 0}
            # Refresh first: the steps below decide on state that only this
            # refresh keeps current.
            await pool.settle_reservations()
            await pool.refresh_due()
            await session.commit()
            await pool.release_undeleted()
            result = await pool.enroll_pending()
            await session.commit()
            await pool.maintain()
            await session.commit()
        return result


class SandboxSweeper:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    async def sweep(self) -> dict[str, int]:
        from app.domain.machine import cloud_vm, retained_archives
        from app.domain.machine.lifecycle import SandboxLifecycle
        from app.domain.machine.microcloud import MicroCloudClient

        if not MicroCloudClient().configured:
            return {"destroyed": 0, "vms_released": 0}
        async with self._sessions() as session:
            lifecycle = SandboxLifecycle(session)
            result = await lifecycle.sweep()
            # A session's whole cloud VM is released by the same idle measure.
            result["vms_released"] = await cloud_vm.release_idle(session, lifecycle)
            result.update(await retained_archives.sweep(session))
            return result


class ComputeMeterSweeper:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    async def sweep(self) -> dict[str, int]:
        from app.domain.machine import metering
        from app.domain.usage.compute import ComputeMeter

        async with self._sessions() as session:
            seen = await metering.observe(session)
            charged = await ComputeMeter(session).settle()
        return {**seen, "charged": charged}
