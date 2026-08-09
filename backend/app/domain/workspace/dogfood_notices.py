"""Post-hoc result notice for the 采纳即上线 dogfood push-back hook.

push_back() starts scripts/on-dogfood-push.sh DETACHED and returns immediately
— the accept API must never block on a real merge + checks + redeploy cycle
(can take minutes). This watches the already-started process in the
background and, once it exits, posts what actually happened (deployed /
rolled back / unknown) into the topic's timeline, so whoever accepted the
card learns the outcome without SSHing into the dev box.

Unlike sandbox_notices' pure best-effort style, a dropped notice here is worse
(it's the only signal the accepted card silently rolled back), so delivery is
retried before giving up.
"""

import asyncio
import logging
import subprocess
import uuid
from pathlib import Path

from app.core.db import async_session_factory
from app.domain.agent.runtime import get_broker
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.topic.repositories import TopicRepository

logger = logging.getLogger(__name__)

_DONE_MARKER = "=== push-back done: platform now runs "
_ROLLBACK_MARKER = "ROLLBACK:"
_CONFLICT_MARKER = "CONFLICT:"
_LOCK_TIMEOUT_MARKER = "another push-back is still running"

# on-dogfood-push.sh has its own 600s lock wait plus unbounded checks (real
# pytest/npm test); this is a backstop so a hung or killed hook still
# produces a notice instead of leaving the topic waiting forever in silence.
WAIT_TIMEOUT_SECONDS = 30 * 60
_RETRY_DELAYS_SECONDS = (0, 5, 30)


async def watch_dogfood_push(
    topic_id: uuid.UUID,
    proc: subprocess.Popen,
    log_path: Path,
    log_offset: int,
    branch: str,
) -> None:
    """Await the detached hook's exit, then post its outcome to the topic
    timeline. Never raises — any failure to determine the outcome still
    produces an "unknown" notice rather than silence."""
    try:
        text = await _wait_for_output(proc, log_path, log_offset)
    except Exception:  # noqa: BLE001 — still notify, just without detail
        logger.exception(
            "failed to collect on-dogfood-push.sh output for topic %s", topic_id
        )
        text = None
    await _post_with_retries(topic_id, _compose_message(text, branch))


async def _wait_for_output(
    proc: subprocess.Popen, log_path: Path, log_offset: int
) -> str | None:
    """None means "couldn't determine the outcome" (timeout or unreadable
    log) — distinct from an empty-but-readable log."""
    loop = asyncio.get_running_loop()
    try:
        await asyncio.wait_for(
            loop.run_in_executor(None, proc.wait), timeout=WAIT_TIMEOUT_SECONDS
        )
    except TimeoutError:
        return None
    try:
        with open(log_path, "rb") as f:
            f.seek(log_offset)
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return None


def _compose_message(text: str | None, branch: str) -> str:
    if text is None:
        return (
            "⚠️ 部署结果未知：采纳后触发的部署脚本没有在预期时间内结束，或日志读取"
            f"失败，无法确认改动（分支 {branch}）是否已部署，请人工检查 "
            "tmp_dogfood_push.log。"
        )
    if _DONE_MARKER in text:
        return f"✅ 部署成功，现在线上运行的是 commit {_extract_commit(text)}。"
    reason = _last_reason_line(text)
    if _CONFLICT_MARKER in reason or _LOCK_TIMEOUT_MARKER in reason:
        return (
            f"❌ 采纳后的部署未能完成：{reason}\n改动保留在 {branch} 分支，没有丢，"
            "需要人工排查。"
        )
    # ROLLBACK, or any other outcome we don't recognize — never claim success.
    return (
        f"⚠️ 检查未通过，已回滚：{reason}\n改动保留在 {branch} 分支，没有丢，"
        "需要人工排查为什么检查没过。"
    )


def _extract_commit(text: str) -> str:
    for line in text.splitlines():
        if _DONE_MARKER in line:
            return line.split(_DONE_MARKER, 1)[1].strip().rstrip("=").strip()
    return "?"


def _last_reason_line(text: str) -> str:
    markers = (_ROLLBACK_MARKER, _CONFLICT_MARKER, _LOCK_TIMEOUT_MARKER)
    for line in reversed(text.splitlines()):
        line = line.strip()
        if any(m in line for m in markers):
            return line
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else "（日志为空）"


async def _post_with_retries(topic_id: uuid.UUID, content: str) -> None:
    last_exc: Exception | None = None
    for delay in _RETRY_DELAYS_SECONDS:
        if delay:
            await asyncio.sleep(delay)
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
                    content=content,
                    kind=BlockKind.event,
                )
                payload = BlockOut.model_validate(block).model_dump(mode="json")
                await session.commit()
            await get_broker().publish(
                str(topic.id), {"type": "event_block", "block": payload}
            )
            return
        except Exception as exc:  # noqa: BLE001 — retry, then give up loudly
            last_exc = exc
    logger.error(
        "failed to post dogfood push-back notice for topic %s after %d attempts: %s",
        topic_id,
        len(_RETRY_DELAYS_SECONDS),
        last_exc,
    )
