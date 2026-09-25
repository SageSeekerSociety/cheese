"""Enrolling provisioned machines.

Deliberately on its own interval and its own switch. Enrolling a machine is
platform plumbing with no judgment in it, and a machine that came up while
nobody was looking must still become a device — so it must not share a switch
with anything a deployment might want off. See
``app.core.background.PeriodicRunner`` for the clock.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from app.core.db import SessionFactory

if TYPE_CHECKING:
    from app.domain.machine.services import FailedLease

logger = logging.getLogger("cheese.machine.runner")


class MachineEnrollmentSweeper:
    def __init__(
        self,
        session_factory: SessionFactory,
        on_ready: Callable[[list[tuple[uuid.UUID, str]]], Awaitable[None]]
        | None = None,
        on_failed: Callable[[list["FailedLease"]], Awaitable[None]] | None = None,
    ) -> None:
        self._sessions = session_factory
        self._on_ready = on_ready
        self._on_failed = on_failed

    async def sweep(self) -> dict[str, int]:
        # Imported here: the machine domain pulls in the device service, and
        # importing it at module scope would drag that into app startup.
        from app.domain.machine.services import MachineService

        async with self._sessions() as session:
            service = MachineService(session)
            if not service.available:
                # No MicroCloud credentials — nothing can have been provisioned,
                # so there is nothing to enroll. Not an error.
                return {"enrolled": 0, "failed": 0}
            # Refresh first: both steps below read state that only a read path
            # ever updated, so without this the sweep decides on whatever was
            # true the last time a human opened the project.
            await service.settle_reservations()
            await service.refresh_unsettled()
            result = await service.enroll_pending()
            ready = await service.ready_topic_devices()
            # A lease MicroCloud has given up on will never appear in `ready`,
            # and the room is still showing 「机器正在创建」 for it. Handed over
            # here rather than left for the next human message to trip on.
            failed = await service.failed_topic_leases()
            await session.commit()
        if self._on_ready is not None and ready:
            await self._on_ready(ready)
        if self._on_failed is not None and failed:
            await self._on_failed(failed)
        return result
