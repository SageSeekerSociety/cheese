"""机器闸门 background runner (spec §4.4/§9, eval C2).

Filing an accept card on a project with a `check_command` creates the card in
`pending_gate` and dispatches this runner. It runs the command in the topic's
workspace (host-side, see workspace.service.run_check_command for the trust
model), then settles the card: green → pending (the reviewer only ever sees a
green card), red → gate_failed + a system nudge so 芝士 goes and fixes it.

The check can take minutes, so it never runs inside a request handler — the
POST returns immediately with the pending_gate card and the UI polls.
"""

import asyncio
import logging
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.workspace import service as ws

logger = logging.getLogger("cheesex.gate")

# Hard ceiling for a project's check command (10 minutes).
GATE_TIMEOUT_S = 600
# Where full check output lands: logs/gate-<topic8>.log (project-local ./logs).
LOG_DIR = Path("logs")

# Keep references so in-flight gate tasks aren't GC'd (same pattern as
# TurnRunner._tasks).
_TASKS: set[asyncio.Task] = set()


def dispatch(
    session_factory: async_sessionmaker,
    chat_service,
    runner,
    *,
    card_id: uuid.UUID,
    topic_id: uuid.UUID,
    project_id: uuid.UUID,
    command: str,
) -> None:
    """Start the gate check in the background; returns immediately."""
    task = asyncio.create_task(
        _run(
            session_factory,
            chat_service,
            runner,
            card_id=card_id,
            topic_id=topic_id,
            project_id=project_id,
            command=command,
        )
    )
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def _run(
    session_factory: async_sessionmaker,
    chat_service,
    runner,
    *,
    card_id: uuid.UUID,
    topic_id: uuid.UUID,
    project_id: uuid.UUID,
    command: str,
) -> None:
    log_path = LOG_DIR / f"gate-{topic_id.hex[:8]}.log"
    try:
        # Both resolve-worktree and the check itself block — keep them off-loop.
        worktree = await asyncio.to_thread(ws.topic_worktree, project_id, topic_id)
        result = await asyncio.to_thread(
            ws.run_check_command,
            worktree,
            command,
            timeout=GATE_TIMEOUT_S,
            log_path=log_path,
        )
    except Exception as exc:  # noqa: BLE001 — a broken gate must fail the card, not hang it
        logger.exception("gate check failed to run for card %s", card_id)
        result = {"exit_code": -1, "tail": f"检查无法执行：{exc}"}

    passed = result["exit_code"] == 0
    tail = str(result["tail"])
    await _settle(session_factory, card_id=card_id, passed=passed, tail=tail)
    logger.info(
        "gate %s for card %s (exit %s, log %s)",
        "passed" if passed else "FAILED",
        card_id,
        result["exit_code"],
        log_path,
    )

    if not passed:
        # 红了 → 芝士收到系统 nudge 去修（same dispatch pattern as the accept
        # merge-conflict resolve turn in routes/accept.py).
        runner.submit(
            chat_service,
            topic_id,
            author="system",
            content=(
                "你递的验收卡没有通过平台的质量检查，卡片没有送到验收人手上。"
                f"检查输出的结尾如下：\n```\n{tail[-1500:]}\n```\n"
                f"完整输出在 {log_path}。请在工作区里修复这些问题，"
                "跑一遍同样的检查确认全绿，然后重新递验收卡。"
            ),
            summon=True,
        )


async def _settle(
    session_factory: async_sessionmaker,
    *,
    card_id: uuid.UUID,
    passed: bool,
    tail: str,
) -> None:
    """Persist the gate result. Retries briefly: the card's INSERT commits in
    the request that dispatched us, which may still be finishing when a very
    fast check (tests) gets here."""
    from app.domain.review.repositories import AcceptCardRepository
    from app.domain.review.services import AcceptService

    for _ in range(100):
        async with session_factory() as session:
            if await AcceptCardRepository(session).get(card_id) is not None:
                await AcceptService(session).finish_gate(
                    card_id=card_id, passed=passed, output_tail=tail
                )
                await session.commit()
                return
        await asyncio.sleep(0.1)
    logger.error("gate result dropped: card %s never became visible", card_id)
