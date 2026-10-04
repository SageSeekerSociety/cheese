"""Which MicroCloud offering the pool's hosts are created from."""

from typing import Any

from app.core.config import settings
from app.core.errors import ValidationError
from app.domain.machine.microcloud import MicroCloudClient


async def pick_offering(client: MicroCloudClient) -> dict[str, Any]:
    offerings = await client.list_offerings()
    if not offerings:
        raise ValidationError(
            "MicroCloud has granted this deployment no offering — an operator "
            "must grant one before machines can be created"
        )
    wanted = settings.microcloud_offering_id
    if wanted:
        for offering in offerings:
            if int(offering["id"]) == wanted:
                return offering
        raise ValidationError(f"configured offering {wanted} is not granted")
    active = [o for o in offerings if o.get("status") == "active"]
    return (active or offerings)[0]
