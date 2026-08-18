"""Courtesy system-event notices for sandbox lifecycle events that aren't turn
failures (see platform_failures.py for the exception-classification kind).

A topic's sandbox container is long-lived and reused across turns (both the
tmux and SDK backends run it as `sleep infinity` and `docker exec`/tmux into
it), so background processes started in one turn (a dev server, a
long-running test run) normally survive between turns — a turn ending or
timing out never touches the container.

**Three** things force-kill the BOX (`docker rm -f`, no grace period), all in
`TmuxHooksProvider._ensure_container`: the sandbox image changed, the routing
env drifted, or the CLI mount went stale. A box serves a whole room now, so
each of those takes every topic in that room down with it.

A FOURTH kills only one topic's tmux session (`_ensure_session`): its hook token
stopped verifying, which makes that topic permanently unable to report anything.
Either way the topic loses its interactive session and every background process
in it, and nothing else tells the user that happened — this posts that missing
notice, and says which of the two actually occurred.

It used to say "镜像已切换" whatever the cause — wrong for two of the four — and
the token case posted nothing at all, so a topic could lose its session in
total silence. The reason is a parameter now, so a new rebuild trigger has to
answer "what does the room get told?" at the call site.
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

# What changed. Kept short and concrete — the consequence is identical in every
# case and is appended once below, so these differ only in the WHY.
REBUILD_CAUSE_TEXT: dict[str, str] = {
    "image": "运行环境镜像已切换",
    "env": "运行环境配置已变更（例如模型路由）",
    "cli_mount": "平台命令行工具已更新",
    # Reached when SANDBOX_TOKEN is rotated under a live session, or the
    # session's token ages past its TTL: its hooks are all rejected and the
    # topic can never report anything again (#316). Restarting the session is
    # the only remedy — and it costs the conversation, which is exactly why it
    # must be said out loud.
    "token": "本话题沙箱的回调令牌已失效",
}

# Which causes take only THIS topic's session, leaving the room's box (and its
# other topics) running. Everything else rebuilds the box itself.
_SESSION_ONLY_CAUSES = frozenset({"token"})

_CONSEQUENCE = (
    "，本话题的沙箱容器已重建：之前运行中的交互会话和任何后台任务"
    "（如后台测试、开发服务器）都被终止了，需要重新启动。已提交的项目文件不受影响。"
)

_SESSION_CONSEQUENCE = (
    "，本话题的交互会话已重启：之前的对话上下文和任何后台任务"
    "（如后台测试、开发服务器）都被终止了，需要重新启动。已提交的项目文件不受影响，"
    "同房间其他话题不受影响。"
)


def rebuild_notice_text(cause: str) -> str:
    """The notice for one rebuild cause. An unknown cause still produces a
    usable sentence — a caller that forgot to register its reason must not be
    the thing that silences the notice — and is treated as a BOX rebuild, the
    larger of the two consequences, so the notice never understates what was
    lost."""
    consequence = (
        _SESSION_CONSEQUENCE if cause in _SESSION_ONLY_CAUSES else _CONSEQUENCE
    )
    return "⚠️ " + REBUILD_CAUSE_TEXT.get(cause, "运行环境已重建") + consequence


async def warn_container_rebuilt(topic_id: uuid.UUID, cause: str = "image") -> None:
    """Persist a system event noting the container was just force-rebuilt.

    Best-effort / fire-and-forget: this is a courtesy notice, never allowed to
    affect the turn it races with.
    """
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
                content=rebuild_notice_text(cause),
                kind=BlockKind.event,
            )
            payload = BlockOut.model_validate(block).model_dump(mode="json")
            await session.commit()
        await get_broker().publish(
            str(topic.id), {"type": "event_block", "block": payload}
        )
    except Exception:  # noqa: BLE001 — best effort, must never break the turn
        logger.exception("failed to post rebuild notice for topic %s", topic_id)
