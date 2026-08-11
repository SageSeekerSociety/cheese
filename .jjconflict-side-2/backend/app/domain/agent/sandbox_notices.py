"""Courtesy system-event notices for sandbox lifecycle events that aren't turn
failures (see platform_failures.py for the exception-classification kind).

A topic's sandbox container is long-lived and reused across turns (both the
tmux and SDK backends run it as `sleep infinity` and `docker exec`/tmux into
it), so background processes started in one turn (a dev server, a
long-running test run) normally survive between turns — a turn ending or
timing out never touches the container. The ONE path that force-kills it
(`docker rm -f`, no grace period) is the project's sandbox image changing:
the next turn on that topic rebuilds the box on the spot and takes everything
running inside it with it, with nothing else telling the user that happened.
This posts that missing notice into the topic's timeline.
"""

import logging
import uuid

from app.core.db import async_session_factory
from app.domain.agent.runtime import get_broker
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.topic.repositories import TopicRepository

logger = logging.getLogger(__name__)

IMAGE_SWITCH_REBUILD_TEXT = (
    "⚠️ 运行环境镜像已切换，本话题的沙箱容器已重建：之前运行中的交互会话和任何后台任务"
    "（如后台测试、开发服务器）都被终止了，需要重新启动。已提交的项目文件不受影响。"
)


async def warn_image_switch_rebuild(topic_id: uuid.UUID) -> None:
    """Persist a system event noting the container was just force-rebuilt
    because the project's sandbox image changed. Best-effort / fire-and-forget:
    this is a courtesy notice, never allowed to affect the turn it races with."""
    try:
        async with async_session_factory() as session:
            topic = await TopicRepository(session).get(topic_id)
            if topic is None:
                return
            block = await BlockRepository(session).add(
                project_id=topic.project_id,
                topic_id=topic.id,
                author="system",
                author_type=AuthorType.system,
                content=IMAGE_SWITCH_REBUILD_TEXT,
                kind=BlockKind.event,
            )
            payload = BlockOut.model_validate(block).model_dump(mode="json")
            await session.commit()
        await get_broker().publish(
            str(topic.id), {"type": "event_block", "block": payload}
        )
    except Exception:  # noqa: BLE001 — best effort, must never break the turn
        logger.exception(
            "failed to post image-switch rebuild notice for topic %s", topic_id
        )
