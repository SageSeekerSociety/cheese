"""Background loop that enrolls provisioned machines.

Deliberately its own loop rather than a step inside the project scheduler. That
scheduler drives 定期巡检 — it spends model budget and makes AI judgment calls,
so deployments keep it off (its interval defaults to 0). Enrolling a machine is
platform plumbing with no judgment in it, and hanging it off that switch would
mean a deployment could not have working machines without also turning on
autonomous patrols. They are separate concerns with separate switches.
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("cheese.machine.runner")


class MachineEnrollmentRunner:
    def __init__(
        self,
        session_factory: Callable[[], AsyncSession],
        interval_seconds: int,
    ) -> None:
        self._sessions = session_factory
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._interval > 0 and self._task is None:
            self._task = asyncio.create_task(self._loop())
            logger.info("machine enrollment sweep started (every %ss)", self._interval)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self.sweep()
            except Exception:  # noqa: BLE001 — a sweep must never kill the loop
                logger.exception("machine enrollment sweep failed")

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
            await service.reconcile_ai_mode()
            result = await service.enroll_pending()
            await session.commit()
        if result["enrolled"] or result["failed"]:
            logger.info(
                "enrollment sweep: %s enrolled, %s failed",
                result["enrolled"],
                result["failed"],
            )
        return result
