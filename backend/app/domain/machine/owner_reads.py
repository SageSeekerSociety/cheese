"""Column-limited cloud host reads for the long-lived device owner.

Why the owner names its columns: ``app/domain/device/owner_reads.py``.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.device.models import DeviceRow
from app.domain.device.supply import Supply
from app.domain.machine.models import CloudHost, CloudHostHome


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


async def sandbox_destroyed(
    session: AsyncSession, device_id: str, resource_id: str | None
) -> bool:
    """Whether the sandbox in ``resource_id`` on the pool host ``device_id`` is
    gone: destroyed while idle, or given up with its host. A session's lease
    still names it until the session's next tool call asks for new hands
    (``machine/lifecycle.py``). False for any machine that is not a pool host."""
    if resource_id is None:
        return False
    host_id = await session.scalar(
        select(CloudHost.id).where(CloudHost.device_id == device_id)
    )
    if host_id is None:
        return False
    return (
        await session.scalar(
            select(CloudHostHome.id).where(
                CloudHostHome.host_id == host_id,
                CloudHostHome.resource_id == resource_id,
            )
        )
        is None
    )
