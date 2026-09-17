"""Room execution over its recorded device connector."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.device_hub import device_hub


async def lock_release(db: AsyncSession, resource_id, *, shared=False) -> None:
    function = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
    await db.execute(
        text(f"SELECT {function}(hashtextextended(:key, 0))"),
        {"key": f"executor-release:{resource_id}"},
    )


async def call(
    target: dict, method: str, params: dict, *, hub=None, trace_id=None
) -> dict:
    hub = hub or device_hub
    return await hub.call_executor(
        target["device_id"],
        target["home"] + "/.cheese/executor",
        method,
        params,
        trace_id=trace_id,
    )
