"""换身体 — when the machine a topic runs on is judged dead, move the topic (#186).

The gap this closes: a turn that dies of a *recognised* platform failure used to be
the one kind of failure that never came back. Ordinary crashes get an automatic
retry; a classified ``platform_failure`` deliberately skipped it, on the reasoning
that retrying cannot conjure disk space. True — but only while there was nowhere
else to go. Once the topic can be moved to another machine, "don't retry here"
has to become "retry somewhere else", or the platform recognises the problem
perfectly and still needs a human to say "go on".

The flow, on a host-scoped failure (``PlatformFailure.host_scoped``):

  1. record it against the DEVICE (not the topic) — ``record_host_failure``;
  2. if that was the second consecutive failure of the same kind, the machine is
     quarantined for a cooldown (``device.health``);
  3. pick another online, non-quarantined machine in the project;
  4. release the topic's pin **explicitly, with a reason**, and re-pin to it;
  5. hand the caller a room-visible message and an auto-resume delay.

Steps 4 and 5 are not decoration. ``bind_topic_device`` is write-once and the
resolver is documented to NEVER fall back to another device, because a topic that
silently woke up elsewhere with an empty work tree was a real bug that was hard to
see. Moving a topic is allowed exactly when it is deliberate, reasoned and said out
loud; this module is the one place that does it.

What is NOT carried across: the resumable ``claude`` session. The new machine
starts a fresh session and restores the work tree from git (every turn commits and
pushes), so what is lost is the uncommitted tail of one turn — not the topic. Any
attempt to "resume" a session that lives on the other machine's disk would be the
drift bug wearing a new hat.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select

from app.domain.agent.platform_failures import PlatformFailure
from app.domain.device.service import DeviceService, device_service_for_session

logger = logging.getLogger(__name__)

# Long enough for the new machine's screen to be opened and the work tree cloned,
# short enough that the room does not look abandoned.
SWAP_RESUME_AFTER_S = 15.0
SWAP_RESUME_REASON = "已换到另一台机器，接着跑"


@dataclass(frozen=True, slots=True)
class SwapOutcome:
    """What the failure handling did, in terms the turn layer can act on.

    ``message`` is room-visible copy (``None`` = nothing worth saying happened);
    ``resume_after_s`` is set only when the topic actually landed somewhere it can
    continue — announcing a swap without rescheduling the turn would leave the
    topic parked on a healthy machine doing nothing."""

    quarantined: bool = False
    old_device: str | None = None
    new_device: str | None = None
    message: str | None = None
    resume_after_s: float | None = None
    resume_reason: str | None = None


NO_SWAP = SwapOutcome()


async def _project_of_topic(session, topic_id: uuid.UUID) -> uuid.UUID | None:
    from app.domain.topic.models import Topic

    return await session.scalar(select(Topic.project_id).where(Topic.id == topic_id))


async def swap_topic_device(
    service: DeviceService,
    *,
    topic_id: uuid.UUID,
    project_id: uuid.UUID | None,
    failure: PlatformFailure,
    is_online: Callable[[str], bool],
) -> SwapOutcome:
    """Account for one host-scoped turn failure and, if the machine is now judged
    dead, move the topic to a healthy one. The whole decision, with no session
    plumbing — ``handle_host_failure`` is the wrapper that opens a session and
    commits.

    Safe to call for any topic: one that never ran on an enrolled machine has no pin
    and gets ``NO_SWAP`` without anything being touched."""
    if not failure.host_scoped:
        # A failure that would follow the topic to any machine says nothing about
        # this one. Counting it would quarantine healthy boxes for a registry
        # outage — and quarantine every box, since they all fail the same way.
        return NO_SWAP
    old_id = await service.topic_device(topic_id)
    if old_id is None:
        return NO_SWAP
    verdict = await service.record_host_failure(old_id, failure.code)
    if not verdict.quarantined:
        # First strike: stay put. One failure is a hiccup, and moving a topic
        # costs the uncommitted tail of a turn.
        return NO_SWAP

    old = await service.get_device(old_id)
    old_name = old.name if old is not None else old_id
    candidates = (
        []
        if project_id is None
        else await service.healthy_devices_for_project(project_id, is_online)
    )
    target = next((d for d in candidates if d.device_id != old_id), None)
    if target is None:
        # Judged dead with nowhere to go. Say so plainly rather than silently
        # leaving the topic pinned to a machine we just took out of rotation — the
        # next @ retries here, and by then the cooldown may have lapsed or another
        # machine may have come online.
        return SwapOutcome(
            quarantined=True,
            old_device=old_id,
            message=(
                f"⚠️ 机器「{old_name}」连续 {verdict.consecutive_failures} 轮"
                f"因「{failure.title}」失败，已暂停向它派活；"
                "但当前没有别的可用机器接手，本话题只能等它恢复。"
                "请稍后再 @芝士，或让管理员加一台机器。"
            ),
        )

    await service.release_topic_device(
        topic_id,
        reason=(
            f"host {old_id} quarantined after {verdict.consecutive_failures} "
            f"consecutive {failure.code} failures; moving to {target.device_id}"
        ),
    )
    await service.bind_topic_device(topic_id, target.device_id)
    logger.warning(
        "topic %s moved from device %s to %s after %s",
        topic_id,
        old_id,
        target.device_id,
        failure.code,
    )
    return SwapOutcome(
        quarantined=True,
        old_device=old_id,
        new_device=target.device_id,
        message=(
            f"🔁 机器「{old_name}」连续 {verdict.consecutive_failures} 轮"
            f"因「{failure.title}」失败，已暂停向它派活；"
            f"本话题已换到「{target.name}」上继续。"
            "代码会从 git 恢复，已提交的改动都在；"
            "上一轮没来得及提交的半成品可能丢失。"
        ),
        resume_after_s=SWAP_RESUME_AFTER_S,
        resume_reason=SWAP_RESUME_REASON,
    )


async def handle_host_failure(
    *,
    topic_id: uuid.UUID,
    failure: PlatformFailure,
    session_factory: Callable | None = None,
    is_online: Callable[[str], bool] | None = None,
    project_id: uuid.UUID | None = None,
) -> SwapOutcome:
    """``swap_topic_device`` against the real database. Never raises — a failure in
    the failure handler must not replace the error the user needs to see."""
    if not failure.host_scoped:
        return NO_SWAP
    try:
        factory = session_factory
        if factory is None:
            from app.core.db import async_session_factory

            factory = async_session_factory
        online = is_online
        if online is None:
            from app.domain.agent.device_hub import device_hub

            online = device_hub.is_online

        async with factory() as session:
            service = device_service_for_session(session)
            outcome = await swap_topic_device(
                service,
                topic_id=topic_id,
                project_id=project_id or await _project_of_topic(session, topic_id),
                failure=failure,
                is_online=online,
            )
            await session.commit()
            return outcome
    except Exception:  # noqa: BLE001 — never mask the failure being handled
        logger.exception("host failure handling failed for topic %s", topic_id)
        return NO_SWAP


async def record_host_success(
    *,
    topic_id: uuid.UUID,
    session_factory: Callable | None = None,
) -> None:
    """A turn got through — clear the machine's failure streak. Best effort: this is
    bookkeeping, and losing it only costs an extra strike later."""
    try:
        factory = session_factory
        if factory is None:
            from app.core.db import async_session_factory

            factory = async_session_factory
        async with factory() as session:
            service = device_service_for_session(session)
            device_id = await service.topic_device(topic_id)
            if device_id is None:
                return
            await service.record_host_success(device_id)
            await session.commit()
    except Exception:  # noqa: BLE001 — bookkeeping must never fail a good turn
        logger.exception("host success bookkeeping failed for topic %s", topic_id)
