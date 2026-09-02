"""A turn failed in a way that blames the MACHINE: account for it, judge it, say so.

The topic stays where it is. There used to be a second half here — 换身体
(#186): a cloud machine judged dead was replaced with a fresh one, or the topic
was re-pinned onto another cloud machine of the project. Retired 2026-09-02: a
swap that succeeds hides the fault that caused it, and the one time it mattered
(2026-08-29, machine 477) what was needed was the reason, not a new machine.
Every failure now ends where it happened, named, with the pin untouched, and a
person decides what to do with the machine.

The flow, on a host-scoped failure (``PlatformFailure.host_scoped``):

  1. record it against the DEVICE (not the topic) — ``record_host_failure``;
  2. if that was the second consecutive failure of the same kind, the machine is
     quarantined for a cooldown (``device.health``);
  3. hand the caller a room-visible message that names the machine and says the
     topic is waiting on it.

The pin is write-once and the resolver NEVER falls back to another device,
because a topic that silently woke up elsewhere with an empty work tree was a
real bug that was hard to see. Nothing in this module moves a topic.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from app.domain.agent.platform_failures import PlatformFailure
from app.domain.agent.platform_notices import (
    EVENT_HOST_FAILURE,
    SEVERITY_WARN,
    WHO_HUMAN,
    notice,
)
from app.domain.device.service import DeviceService, device_service_for_session
from app.domain.device.supply import Supply

logger = logging.getLogger(__name__)


def _failure_meta(failure: PlatformFailure, verdict, detail: str) -> dict:
    """机器连续失败事件的 `meta`。失败次数和原因是**展开区**的内容，不是那一行。"""
    return notice(
        EVENT_HOST_FAILURE,
        severity=SEVERITY_WARN,
        who=WHO_HUMAN,
        detail=(
            f"连续 {verdict.consecutive_failures} 轮因「{failure.title}」失败。\n"
            f"{detail}"
        ),
        detail_label="发生了什么",
    )


@dataclass(frozen=True, slots=True)
class HostVerdict:
    """What the failure accounting concluded, in terms the turn layer can act on.

    ``message`` is room-visible copy (``None`` = nothing worth saying happened:
    one failure is a hiccup, and the room already carries the failure itself)."""

    quarantined: bool = False
    device_id: str | None = None
    message: str | None = None
    event_meta: dict | None = None


NO_VERDICT = HostVerdict()


async def judge_host_failure(
    service: DeviceService,
    *,
    topic_id: uuid.UUID,
    failure: PlatformFailure,
) -> HostVerdict:
    """Account for one host-scoped turn failure and, if the machine is now judged
    dead, say so. The whole decision, with no session plumbing —
    ``handle_host_failure`` is the wrapper that opens a session and commits.

    Safe to call for any topic: one that never ran on an enrolled machine has no
    pin and gets ``NO_VERDICT`` without anything being touched."""
    if not failure.host_scoped:
        # A failure that would follow the topic to any machine says nothing about
        # this one. Counting it would quarantine healthy boxes for a registry
        # outage — and quarantine every box, since they all fail the same way.
        return NO_VERDICT
    binding = await service.topic_binding(topic_id)
    if binding is None:
        return NO_VERDICT
    device_id = binding.device_id
    verdict = await service.record_host_failure(device_id, failure.code)
    if not verdict.quarantined:
        # First strike: one failure is a hiccup, and the room already shows it.
        return NO_VERDICT

    device = await service.get_device(device_id)
    name = device.name if device is not None else device_id
    if device is not None and device.supply is Supply.cloud:
        return HostVerdict(
            quarantined=True,
            device_id=device_id,
            message=f"Cloud 机器「{name}」连续失败",
            event_meta=_failure_meta(
                failure,
                verdict,
                "本话题留在这台机器上，平台不会换一台。"
                "需要有人看这台机器上的连接器和屏幕，修好后再 @芝士。",
            ),
        )
    return HostVerdict(
        quarantined=True,
        device_id=device_id,
        message=f"机器「{name}」连续失败，已暂停派活",
        event_meta=_failure_meta(
            failure,
            verdict,
            "本话题仍留在这台机器上，平台会等它恢复，不会迁移到别的机器。"
            "请在机器恢复后再 @芝士。",
        ),
    )


async def handle_host_failure(
    *,
    topic_id: uuid.UUID,
    failure: PlatformFailure,
    session_factory: Callable | None = None,
) -> HostVerdict:
    """``judge_host_failure`` against the real database. Never raises — a failure
    in the failure handler must not replace the error the user needs to see."""
    if not failure.host_scoped:
        return NO_VERDICT
    try:
        factory = session_factory
        if factory is None:
            from app.core.db import async_session_factory

            factory = async_session_factory
        async with factory() as session:
            service = device_service_for_session(session)
            verdict = await judge_host_failure(
                service, topic_id=topic_id, failure=failure
            )
            await session.commit()
            return verdict
    except Exception:  # noqa: BLE001 — never mask the failure being handled
        logger.exception("host failure handling failed for topic %s", topic_id)
        return NO_VERDICT


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
