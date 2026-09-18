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


# What a placement recorded before the installation reported its own state
# directory means. FROZEN: it is not "the root in force today" but "the root the
# backend that wrote that placement would have named", so it must not follow the
# platform's install root the next time that moves. Every turn rewrites the
# placement, so nothing reaches this for longer than one turn.
UNRECORDED_EXECUTOR_STATE = "/.cheese/executor"


def executor_state(target: dict) -> str:
    """Where this room's executor is, as the installation itself reported it.

    Not derived here, deliberately. This module runs in TWO processes that are
    released separately: the business backend, which every app deploy replaces,
    and the device connection owner, which an app deploy deliberately leaves
    alone. The owner is the one that matters — it serves
    ``/topics/{id}/execution/{id}`` (see ``device_connection_app``), the endpoint
    every room's tool call and every bootstrap ping comes through. So a path
    spelled here is spelled twice, in two builds that can be weeks apart, and
    the two halves disagreeing is not a crash anybody can read: it is a room
    whose executor cannot be found at all, under an error naming a directory
    that is correct in the other build.

    Carrying the answer in the placement makes the owner's build irrelevant to
    it. That is not a new idea here — it is why the harnesses that record their
    own state path came through the move of the install root untouched while
    this one did not.
    """
    recorded = target.get("state")
    return recorded if recorded else target["home"] + UNRECORDED_EXECUTOR_STATE


async def call(
    target: dict, method: str, params: dict, *, hub=None, trace_id=None
) -> dict:
    hub = hub or device_hub
    return await hub.call_executor(
        target["device_id"],
        executor_state(target),
        method,
        params,
        trace_id=trace_id,
    )
