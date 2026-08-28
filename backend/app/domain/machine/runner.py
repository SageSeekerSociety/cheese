"""Enrolling provisioned machines.

Deliberately on its own interval rather than a step inside the project
scheduler. That scheduler drives 定期巡检 — it spends model budget and makes AI
judgment calls, so deployments keep it off (its interval defaults to 0).
Enrolling a machine is platform plumbing with no judgment in it, and hanging it
off that switch would mean a deployment could not have working machines without
also turning on autonomous patrols. They are separate concerns with separate
switches — see ``app.core.background.PeriodicRunner`` for the clock.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable

from app.core.db import SessionFactory

logger = logging.getLogger("cheese.machine.runner")


class MachineEnrollmentSweeper:
    def __init__(
        self,
        session_factory: SessionFactory,
        on_ready: Callable[[list[tuple[uuid.UUID, str]]], Awaitable[None]]
        | None = None,
    ) -> None:
        self._sessions = session_factory
        self._on_ready = on_ready

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
            # Converge the built-in AI channel (→ccproxy) BEFORE enrolling, not
            # after. Enrollment is the one moment the platform is on the machine
            # over ssh, and what it reads there only exists once the channel has
            # settled — so the old order enrolled a machine at the provisioning
            # default and switched it a step too late, losing its ccproxy
            # identity permanently (observed live 2026-08-14, machine 472).
            # Both halves are needed: this starts the switch, and enrolment waits
            # for it to land (`list_awaiting_enrollment`, bounded).
            # Refresh first: both steps below read state that only a read path
            # ever updated, so without this the sweep decides on whatever was
            # true the last time a human opened the project.
            await service.refresh_unsettled()
            await service.reconcile_ai_mode()
            result = await service.enroll_pending()
            ready = await service.ready_topic_devices()
            await session.commit()
        if self._on_ready is not None and ready:
            await self._on_ready(ready)
        if result["enrolled"] or result["failed"]:
            logger.info(
                "enrollment sweep: %s enrolled, %s failed",
                result["enrolled"],
                result["failed"],
            )
        return result
