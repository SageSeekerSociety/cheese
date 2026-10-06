"""Column-limited cloud host reads for the long-lived device owner.

Why the owner names its columns: ``app/domain/device/owner_reads.py``.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.device.models import DeviceRow
from app.domain.device.supply import Supply
from app.domain.machine.models import CloudHost


async def active_cloud_host(session: AsyncSession, device_id: str) -> bool:
    """Whether this device is a host of the pool. A host carries the sandboxes
    of sessions from any project, so the session's own lease on it — checked
    by the caller — is what ties a call to the project."""
    return (
        await session.scalar(
            select(CloudHost.device_id)
            .join(DeviceRow, DeviceRow.device_id == CloudHost.device_id)
            .where(
                CloudHost.device_id == device_id,
                CloudHost.released_at.is_(None),
                DeviceRow.supply == Supply.cloud,
            )
        )
        is not None
    )
