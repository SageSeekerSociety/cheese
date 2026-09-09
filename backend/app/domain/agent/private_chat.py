"""Private chat routing and controls over the central device connection."""

import asyncio
import json
import uuid

from app.core.config import settings
from app.domain.agent.device_hub import device_hub
from app.domain.agent.device_provider import device_home_dir
from app.domain.agent.harness.claude_code.remote_execution.client import RemoteClient
from app.domain.agent.harness.claude_code.remote_execution.private import target
from app.domain.topic.services import TopicService


def execution_target(
    project_id: uuid.UUID, topic_id: uuid.UUID, resource_id: uuid.UUID | None = None
) -> dict:
    if not settings.private_chat_device_id:
        raise RuntimeError("私聊中心执行机尚未配置，本轮没有启动")
    return {
        **target(resource_id or topic_id, settings.private_chat_executor_image),
        "device_id": settings.private_chat_device_id,
        "home": device_home_dir(project_id, resource_id or topic_id),
    }


async def for_topic(db, topic_id: uuid.UUID) -> dict | None:
    place = await TopicService(db).place_or_404(topic_id)
    await db.commit()
    if place.room.is_private:
        return execution_target(place.project_id, place.room_id, place.room.resource_id)
    return settings.agent_execution_targets.get(str(topic_id))


async def control(target: dict, payload: dict, *, hub=None) -> dict:
    if target.get("kind") != "private":
        return await asyncio.to_thread(RemoteClient(target).control, payload)
    # home contains only a literal $HOME followed by server-generated UUID paths.
    home = target["home"]
    command = (
        f'exec python3 "{home}/.claude/remote-execution/client.py" control '
        f'"{home}/.claude/remote-session/execution.json"'
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
    result = await hub.exec(
        device_id,
        [
            "sh",
            "-c",
            f'if test -f "{home}/.claude/remote-target.json"; then '
            f'exec python3 "{home}/.claude/remote-execution/client.py" release '
            f'"{home}/.claude/remote-target.json"; fi',
        ],
        timeout=45,
    )
    if result.get("exit") != 0:
        raise RuntimeError(result.get("stderr") or "Private scratch cleanup failed")
