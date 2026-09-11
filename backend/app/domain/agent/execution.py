"""Room execution over its recorded device connector."""

from app.domain.agent.device_hub import device_hub


async def call(
    target: dict, method: str, params: dict, *, hub=None, trace_id=None
) -> dict:
    hub = hub or device_hub
    return await hub.call_executor(
        target["device_id"],
        target["home"] + "/.claude/executor",
        method,
        params,
        trace_id=trace_id,
    )
