"""Scan durable work; all completion I/O runs outside database transactions."""

import logging
from collections.abc import Awaitable, Callable

from app.core.errors import AppError, BaseError
from app.domain.doc_ai.completion import Completion, InvalidCompletion
from app.domain.doc_ai.routing import admit
from app.domain.doc_ai.services import DocAiService, Lease

logger = logging.getLogger(__name__)


async def run_one(
    sessions,
    invoke: Callable[[Lease], Awaitable[Completion]],
    meter: Callable[[Lease], Awaitable[None]],
) -> bool:
    async with sessions() as session:
        lease = await DocAiService(session).claim_next()
        await session.commit()
    if lease is None:
        return False
    result = None
    usage = None
    error = None
    try:
        async with sessions() as session:
            await admit(session, lease.project_id, lease.binding)
        completion = await invoke(lease)
        result, usage = completion.result, completion.usage
    except InvalidCompletion as exc:
        usage, error = exc.usage, str(exc)
    except (AppError, BaseError) as exc:
        error = str(exc)
    except Exception:
        # Never persist HTTP exception text containing upstream credentials.
        logger.exception("document completion failed for %s", lease.request_id)
        error = "文档模型调用失败，请重新请求"
    async with sessions() as session:
        await DocAiService(session).settle(
            lease, result=result, usage=usage, error=error
        )
        await session.commit()
    # The existing project-key cumulative meter owns credit deduction. Recording
    # attempt JSON above must not charge the same gateway spend a second time.
    await meter(lease)
    return True
