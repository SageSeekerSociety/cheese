"""Private chat routing and controls over the central device connection."""

import asyncio
import json
import uuid

from app.core.config import settings
from app.domain.agent import execution
from app.domain.agent.device_hub import device_hub
from app.domain.agent.device_provider import device_home_dir
from app.domain.agent.executor_transport import RemoteClient
from app.domain.agent.harness.claude_code import (
    private_execution_target as target,
)
from app.domain.agent.place import session_platform_dirs


def scratch_target(
    project_id: uuid.UUID,
    resource_id: uuid.UUID,
    *,
    device_id: str,
) -> dict:
    """这条会话自己的草稿区：一个有界的一次性容器 (结论 19)。

    它不是一个地点，所以它不去解析一台机器——机器是这条会话的机器，由调用者从
    会话行上读出来交进来。
    """
    return {
        **target(
            resource_id,
            settings.private_chat_executor_image,
            settings.fetch_dns_over_https,
        ),
        "device_id": device_id,
        "home": device_home_dir(project_id, resource_id),
    }


async def control(
    target: dict, payload: dict, *, hub=None, trace_id: str | None = None
) -> dict:
    if target.get("kind") == "device":
        return await execution.call(
            target, "control", payload, hub=hub, trace_id=trace_id
        )
    if target.get("kind") != "private":
        return await asyncio.to_thread(RemoteClient(target).control, payload)
    # home contains only a literal $HOME followed by server-generated UUID paths.
    home = target["home"]
    # Every root the platform has installed into, the current one first: a room
    # prepared before the last move still has its client and its session marker
    # where that launcher put them, and a command that names one root only goes
    # quietly nowhere on it.
    # Within a root, the session that prepared the scratch is one seat among the
    # room's (`place.seat_dir`), and a room prepared before seats existed keeps
    # its one execution config at the room level. Any of them names the same
    # private executor: a private executor is one container per topic, not per
    # seat, so which config is found first does not matter.
    roots = " ".join(f'"{home}/{d}"' for d in session_platform_dirs())
    command = (
        f"for root in {roots}; do "
        'for config in "$root/remote-session/execution.json" '
        '"$root/seats"/*/remote-session/execution.json; do '
        'if test -f "$config"; then '
        'seat="$(dirname "$(dirname "$config")")"; '
        'exec python3 "$seat/remote-execution/client.py" control "$config"; fi; '
        "done; done; "
        'echo "no private executor is installed for this room" >&2; exit 1'
    )
    result = await (hub or device_hub).exec(
        target["device_id"],
        ["sh", "-c", command],
        stdin=json.dumps(payload),
        timeout=660,
    )
    if result.get("exit") != 0 or result.get("truncated"):
        raise RuntimeError(result.get("stderr") or "Private executor control failed")
    return json.loads(result["stdout"])


async def release(project_id, topic_id, device_id, hub):
    home = device_home_dir(project_id, topic_id)
    # The target is written by the launcher into the seat that owns the session
    # (`place.seat_dir`), at the room level only for a room prepared before
    # seats existed. Every seat of the room names the same container: a private
    # executor is one container per topic, so removing it once is removing it.
    roots = " ".join(f'"{home}/{d}"' for d in session_platform_dirs())
    result = await hub.exec(
        device_id,
        [
            "sh",
            "-c",
            f"for root in {roots}; do "
            'for config in "$root/remote-target.json" '
            '"$root/seats"/*/remote-target.json; do '
            'if test -f "$config"; then '
            'seat="$(dirname "$config")"; '
            'exec python3 "$seat/remote-execution/client.py" release "$config"; '
            "fi; done; done",
        ],
        timeout=45,
    )
    if result.get("exit") != 0:
        raise RuntimeError(result.get("stderr") or "Private scratch cleanup failed")
