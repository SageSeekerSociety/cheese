"""What a Cloud machine can be asked for right now.

Two ranges meet here. The provider's offering says what MicroCloud will build;
the platform's `ComputeChoice` says what a choice may hold. A person can only
get what both allow, so the range a form offers is their intersection, and the
provider's own numbers are reported beside it rather than in its place.

The offering carries no remaining capacity, so a spec inside this range can
still fail to be created. Nothing here claims otherwise, and when the offering
cannot be read there is no range to show: it is reported unknown, never filled
in from defaults.
"""

from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.core.errors import ValidationError
from app.core.sentences import say
from app.domain.agent.compute_configs import PLATFORM_BOUNDS, ComputeChoice
from app.domain.machine.microcloud import MicroCloudClient, MicroCloudError

# choice field -> (offering min key, offering max key, how a person reads it)
FIELDS: dict[str, tuple[str, str, str]] = {
    "cores": ("coresMin", "coresMax", "CPU 核数"),
    "memory_mb": ("memoryMbMin", "memoryMbMax", "内存"),
    "disk_gb": ("diskGbMin", "diskGbMax", "磁盘"),
}


def describe(name: str, value: int) -> str:
    if name == "memory_mb":
        return f"{value / 1024:g} GB"
    if name == "disk_gb":
        return f"{value} GB"
    return f"{value} 核"


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


@dataclass(frozen=True)
class SupplyRange:
    offering: str
    provider: dict[str, tuple[int, int]]
    selectable: dict[str, tuple[int, int] | None]

    @classmethod
    def of(cls, offering: dict[str, Any]) -> "SupplyRange":
        platform = PLATFORM_BOUNDS
        provider, selectable = {}, {}
        for name, (lo_key, hi_key, _) in FIELDS.items():
            lo, hi = int(offering[lo_key]), int(offering[hi_key])
            provider[name] = (lo, hi)
            both = (max(lo, platform[name][0]), min(hi, platform[name][1]))
            selectable[name] = both if both[0] <= both[1] else None
        label = offering.get("machineTypeName") or str(offering.get("id", ""))
        return cls(offering=str(label), provider=provider, selectable=selectable)

    def problems(self, values: dict[str, int | None]) -> list[str]:
        """One sentence per field that the range cannot honour."""
        out = []
        for name, (_, _, label) in FIELDS.items():
            value = values.get(name)
            if value is None:
                continue
            allowed = self.selectable[name]
            if allowed is None:
                out.append(f"{label}当前没有可选值")
            elif not allowed[0] <= value <= allowed[1]:
                out.append(
                    f"{label} {describe(name, value)} 不在 "
                    f"{describe(name, allowed[0])}–{describe(name, allowed[1])} 之间"
                )
        return out

    def require(self, values: dict[str, int | None]) -> None:
        problems = self.problems(values)
        if problems:
            raise ValidationError(
                say("machineConfigOutOfSupply", problems="；".join(problems))
            )

    def as_json(self) -> dict[str, Any]:
        def pair(p):
            return None if p is None else {"min": p[0], "max": p[1]}

        return {
            "available": True,
            "offering": self.offering,
            "selectable": {k: pair(v) for k, v in self.selectable.items()},
            "provider": {k: pair(v) for k, v in self.provider.items()},
            "capacity_known": False,
        }


async def read_supply(client: MicroCloudClient | None = None) -> dict[str, Any]:
    """The range as an API answer: known, or plainly unknown with the reason."""
    client = client or MicroCloudClient()
    if not client.configured:
        return {"available": False, "reason": "云端尚未接入，暂不可用"}
    try:
        return SupplyRange.of(await pick_offering(client)).as_json()
    except (MicroCloudError, ValidationError) as exc:
        return {"available": False, "reason": str(exc)}


async def check_choice(choice: ComputeChoice) -> None:
    """Refuse a custom cloud spec the current supply cannot honour.

    Only when the range can be read: an unreadable offering leaves the choice to
    the check made when the machine is created, which reads it again.
    """
    values = {name: getattr(choice, name) for name in FIELDS}
    if choice.profile != "cloud" or all(v is None for v in values.values()):
        return
    client = MicroCloudClient()
    if not client.configured:
        return
    try:
        supply = SupplyRange.of(await pick_offering(client))
    except (MicroCloudError, ValidationError):
        return
    supply.require(values)
