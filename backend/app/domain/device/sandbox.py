"""Whether a machine a person enrolled can give a session a sandbox.

Settled when the machine connects, and again when its owner asks, by running
on it the check its install runs (`executor_launch.sandbox_check_script`), and
kept (`DeviceSandboxRow`): the automatic pick passes over a machine that said
it cannot (`DeviceService.refusing_sandboxes`), a room choosing it is told so
before anything is installed, and its owner reads the machine's own reason on
the devices page, in their language. A Cloud machine is the platform's, which
adds what its sandbox needs, so it is never asked.
"""

import json
import logging

from app.domain.device.repository import Device
from app.domain.device.service import DeviceService
from app.domain.device.supply import Supply

logger = logging.getLogger(__name__)


async def ask(hub, device_id: str, program: str) -> tuple[bool, dict | None] | None:
    """Run the check on the machine: whether it can, and if not the sentence
    for its owner about why (``{"key", "params"}``, the `apiError` catalog's).
    None when the machine gave no answer to read, which records nothing.
    Raises what ``hub.exec`` raises when the machine is not there."""
    result = await hub.exec(device_id, ["python3", "-"], stdin=program, timeout=90)
    try:
        answer = json.loads(str(result.get("stdout") or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        answer = None
    if result.get("exit") != 0 or not isinstance(answer, dict):
        logger.warning(
            "sandbox check gave no answer on %s: %s",
            device_id,
            str(result.get("stderr") or "")[-500:],
        )
        return None
    said = answer.get("said")
    return bool(answer.get("isolates")), said if isinstance(said, dict) else None


async def refresh(service: DeviceService, hub, device: Device, program: str) -> None:
    """Ask a machine a person enrolled and keep its answer."""
    if device.supply is not Supply.self_hosted:
        return
    answer = await ask(hub, device.device_id, program)
    if answer is not None:
        isolates, said = answer
        await service.record_sandbox_check(
            device.device_id, isolates=isolates, reason=kept(said)
        )


def kept(said: dict | None) -> str | None:
    """The sentence a machine said, as `device_sandbox.reason` keeps it."""
    return json.dumps(said)[:1000] if said else None


def status(check) -> dict | None:
    """What the machine last said, for its owner to read; None when it has not
    been asked yet."""
    if check is None:
        return None
    return {
        "isolates": check.isolates,
        # The sentence for its owner, rendered in their language.
        "reason": _sentence(check.reason),
        "checked_at": check.checked_at.isoformat(),
    }


def _sentence(reason: str | None) -> dict | None:
    """``reason`` as the sentence it keeps, or None for a row whose reason is
    not one: English text written before reasons were keyed, which the release
    still serving during a deploy can write too. The devices page asks the
    machine again as it opens, which replaces it."""
    try:
        said = json.loads(reason or "")
    except ValueError:
        return None
    return said if isinstance(said, dict) and isinstance(said.get("key"), str) else None
