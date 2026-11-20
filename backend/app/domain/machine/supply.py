"""Which MicroCloud offering the pool's hosts, and whole cloud VMs, are created
from."""

from typing import Any

from app.core.config import settings
from app.core.errors import ValidationError
from app.domain.machine.microcloud import MicroCloudClient, MicroCloudError


async def pick_offering(client: MicroCloudClient, wanted: int = 0) -> dict[str, Any]:
    """The granted offering with id ``wanted``, else the pool's: the one
    ``microcloud_offering_id`` names, or the first active one."""
    offerings = await client.list_offerings()
    if not offerings:
        raise ValidationError(
            "MicroCloud has granted this deployment no offering — an operator "
            "must grant one before machines can be created"
        )
    wanted = wanted or settings.microcloud_offering_id
    if wanted:
        for offering in offerings:
            if int(offering["id"]) == wanted:
                return offering
        raise ValidationError(f"configured offering {wanted} is not granted")
    active = [o for o in offerings if o.get("status") == "active"]
    return (active or offerings)[0]


def effective_spec(
    offering: dict[str, Any], *, cores: int, memory_mb: int, disk_gb: int
) -> dict[str, int]:
    """The size a machine actually gets from ``offering``: each value asked
    for, clamped into the offering's own range. The body sent to MicroCloud and
    the image version recorded against the row both come from here, so a host is
    never recorded as a size it did not get."""
    return {
        name: max(int(offering[f"{name}Min"]), min(int(offering[f"{name}Max"]), value))
        for name, value in (
            ("cores", cores),
            ("memoryMb", memory_mb),
            ("diskGb", disk_gb),
        )
    }


def data_disk_gb(offering: dict[str, Any]) -> int:
    """The data disk ``offering`` gives each of its machines, in GB, which a
    pool host keeps its sandboxes' homes on (``enrollment.DATA_DISK``). An
    offering that gives none cannot give the pool a host: no sandbox runs
    without a disk limit (``sandbox_host.py``), and that limit needs the disk."""
    size = int(offering.get("dataDiskGb") or 0)
    if size <= 0:
        # i18n-exempt: a provider configuration fault, logged for operators
        raise MicroCloudError(
            f"offering {offering.get('id')} gives its machines no data disk for "
            "the sandboxes' homes"
        )
    return size
