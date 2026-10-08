"""Execute a prepared frame source; retain watchdog and exception boundaries.

Scheduling, admission and recovery decisions belong to the owner/intake. This
executor mutates the owner's exact liveness tables and awaits explicit effects.
It never imports a root service, asks a locator for one, or creates a task.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import BaseError
from app.core.obs import bind_context, clear_context
from app.core.sentences import error_frame, say
from app.domain.agent.platform_failures import (
    HOST_SCOPED_CODES,
    SUBSCRIPTION_CREDENTIAL_EXPIRED,
    PlatformFailure,
    classify_platform_failure,
)
from app.domain.agent.platform_notices import (
    EVENT_TURN_FAILED,
    EVENT_TURN_TIMEOUT,
    SEVERITY_ERROR,
    SEVERITY_WARN,
    WHO_HUMAN,
    notice,
)
from app.domain.agent.turn.state.execution import ExecutionPolicy, ExecutionState
from app.domain.agent.turn.store.intervals import close_turns, open_turn

logger = logging.getLogger("cheesex.runtime")


class SystemEventWriter(Protocol):
    async def __call__(
        self,
        topic_id: uuid.UUID,
        content: str,
        turn_id: uuid.UUID,
        /,
        *,
        meta: dict | None = None,
    ) -> dict | None: ...


@dataclass(frozen=True, slots=True)
class ExecutionEffects:
    post_system_event: SystemEventWriter
    stamp_delivery: Callable[[uuid.UUID], Awaitable[None]]
    recover_silent_turn: Callable[[uuid.UUID, str, uuid.UUID], Awaitable[None]]
    record_host_success: Callable[[uuid.UUID], Awaitable[None]]
    handle_host_failure: Callable[
        [uuid.UUID, uuid.UUID, PlatformFailure], Awaitable[None]
    ]
    credential_is_known_expired: Callable[[uuid.UUID], bool]
    session_took_over: Callable[[uuid.UUID, uuid.UUID], bool]


async def execute(
    state: ExecutionState,
    policy: ExecutionPolicy,
    effects: ExecutionEffects,
    sessions: async_sessionmaker[AsyncSession],
    publish: Callable[[str, dict], Awaitable[None]],
    frame_factory: Callable[[], AsyncIterator[dict]],
    topic_id: uuid.UUID,
    turn_id: uuid.UUID,
    *,
    author: str,
    content: str,
    summon: bool,
    is_resume: bool = False,
    resume_reason: str | None = None,
    continuation_id: uuid.UUID | None = None,
    frames: AsyncIterator[dict] | None = None,
    lifecycle: dict[str, bool] | None = None,
) -> None:
    channel = str(topic_id)
    lifecycle = (
        lifecycle
        if lifecycle is not None
        else {
            "started": False,
            "session_owned": False,
        }
    )
    continuation_id = continuation_id or turn_id
    # #388 缺陷一: set when the backend already knows this turn's credential is
    # dead. Read in the fuse-arming block (to cut the fuse short) and again in
    # the TimeoutError handler (to say the true reason and skip the auto-retry).
    # Bound here so it is always defined, even on an early failure path.
    credential_expired = False
    # Did this turn blame the MACHINE? Decides whether finishing counts as
    # evidence the machine is healthy (#186) — a turn that ends in a
    # host-scoped failure must not immediately clear the streak it just added.
    host_failed = False
    # Correlate: every log line anywhere inside this turn carries these ids.
    bind_context(turn=str(turn_id)[:8], topic=str(topic_id)[:8])
    t0 = time.monotonic()
    rec = {
        "turn_id": str(turn_id),
        "topic_id": str(topic_id),
        "continuation_id": str(continuation_id),
        "author": author,
        "summon": summon,
        "is_resume": is_resume,
        "status": "running",
        "started_at": time.time(),
        "first_output_s": None,
        "tools": 0,
        "duration_s": None,
        "detail": None,
    }
    state.recent.append(rec)
    # `_live` FIRST, and only then the durable row. Opening the interval is
    # a database round-trip, so a sweep can land in the gap — and a sweep
    # that sees this turn in `_live` and not in the table finds nothing to
    # claim, where the other order would show it a row with no task and read
    # it as a corpse. `SWEEP_MIN_AGE_S` is what actually protects the window;
    # a just-registered turn is too young to be judged either way.
    current = asyncio.current_task()
    if current is not None:
        state.live[str(turn_id)] = current
    state.last_frame_at[str(turn_id)] = time.monotonic()
    state.live_topics[str(turn_id)] = topic_id
    # The durable interval: if the PROCESS dies (deploy past the drain
    # ceiling, crash), startup finds it still open — a killed turn must
    # never just vanish.
    await open_turn(
        sessions,
        turn_id=turn_id,
        conversation_id=topic_id,
        continuation_id=continuation_id,
        author=author,
        content=content,
        is_resume=is_resume,
        resendable=bool(content.strip())
        and (not is_resume or resume_reason == policy.resend_reason),
        started_at=datetime.now(UTC),
    )
    logger.info("turn start: author=%s summon=%s resume=%s", author, summon, is_resume)
    try:
        # Wall-clock ceiling (R8): a wedged turn must not hold the topic lock
        # forever. On timeout the async-for exits, closing the converse
        # generator → its `async with` blocks unwind → the topic lock releases
        # and the in-container claude process is torn down.
        #
        # This wrap is transport-INDEPENDENT — one AgentWorkRunner singleton, same
        # `policy.timeout` for every backend (SDK / tmux / device). Most
        # backends have no activity signal of their own, so this stays their
        # only ceiling. A driven session has one (turn 活跃度检测: the
        # runtime's own liveness rules and hard ceiling can run well past
        # `policy.timeout`) — it signals its actual ceiling back via
        # a `turn_ceiling` frame, and ONLY that reschedules this wrap
        # (`Timeout.reschedule`), relative to when the turn started so a late
        # frame can't silently grant more time than the backend promised.
        # Every other backend never emits this frame, so their behaviour here
        # is byte-for-byte unchanged.
        loop_start = asyncio.get_running_loop().time()
        # When the turn itself began, filled in by `prompt_delivered` below.
        # `loop_start` is not that moment: preparing the database, choosing a
        # backend and attaching the screen all happen after it.
        turn_start: float | None = None
        # Past `turn_start + ceiling_s` the turn is RECORDED as long, and
        # nothing else: the ceiling stopped being a deadline. Elapsed time
        # cannot tell an agent three hours into a refactor from a session
        # that is stuck, and the harness monitor now carries three checks
        # that each catch a specific way of being stuck (a process gone,
        # output with no tool call, an injected message never read). What a
        # wall clock alone could still end here is a turn that is working
        # and has not finished. So the only deadline this wrap keeps is the
        # cold-start fuse below, which asks a different question.
        ceiling_crossed = False

        # 冷启动看门狗: until the model has said ANYTHING, the wrap runs on a
        # much shorter fuse than the turn ceiling.
        #
        # A turn whose substrate never comes up is indistinguishable, from
        # out here, from one thinking hard — both are silence. So the ceiling
        # (900s for tmux) was what ended them, and for 900 seconds the topic
        # reported 进行中 while nothing existed to make progress. On
        # 2026-08-12 that took the whole dev platform down for 30 minutes:
        # every topic's turn started in the same second, ran with `tools=0`
        # and `first_output_s=None`, and each one occupied its full ceiling
        # before failing. The information needed to call it was there from
        # second one.
        #
        # The fuse only covers the gap BEFORE first output; once a `tool` or
        # `assistant_block` arrives the deadline is pushed out to the real
        # ceiling and this layer is gone for the rest of the turn. So a slow
        # turn is never cut short — only a turn that never started.
        #
        # `turn_ceiling` deliberately does NOT lift the fuse: chat.py emits it
        # up front, before the container is touched, so it proves a backend
        # was selected and nothing more. It is remembered and applied at first
        # output instead.
        first_output_fuse_s = policy.first_output_timeout
        ceiling_s = policy.timeout
        # #388 缺陷一: when the backend already knows this turn's model
        # credential is expired, the turn cannot produce a token — every
        # request is rejected (401/407) before a hook can flow. Don't spend the
        # full cold-start fuse guessing at container/disk/network: cut it to a
        # short grace (still long enough that a credential refreshed between
        # setup and now speaks first, retiring the fuse) so a silent turn fails
        # FAST and with the true reason. The definitive first-hand 401 lives in
        # the metering proxy (box infra, out of this repo); this is the part the
        # backend lands on its own from the expiry #386 already stamps.
        credential_expired = effects.credential_is_known_expired(topic_id)
        if credential_expired and first_output_fuse_s:
            first_output_fuse_s = min(
                first_output_fuse_s, policy.credential_expired_fuse
            )
        if first_output_fuse_s:
            fuse_deadline = loop_start + min(first_output_fuse_s, policy.timeout)
        else:
            fuse_deadline = None
        # No deadline until the fuse asks for one. `policy.timeout` used to
        # be the starting value, standing in for a ceiling the backend had
        # not declared yet; the ceiling is recorded now, never scheduled, so
        # a wrap that started at `policy.timeout` would cut every turn whose
        # fuse is disabled at exactly that mark, with nothing to say why.
        async with asyncio.timeout(None) as turn_deadline:
            if fuse_deadline is not None:
                turn_deadline.reschedule(fuse_deadline)
            turn_frames = frames if frames is not None else frame_factory()
            async for frame in turn_frames:
                kind = frame.get("type")
                if kind == "session_lifecycle":
                    # Interactive providers hand lifecycle to the live
                    # subscription. Their request returns after injection;
                    # Stop or the session watchdog retires the indicator.
                    lifecycle["session_owned"] = True
                    turn_deadline.reschedule(None)
                    continue
                # Proof of life for the silence check in sweep_orphans, taken
                # before the `continue`s below so EVERY frame counts. A tool
                # call persists no Block, so without this a turn legitimately
                # grinding through tools looks identical to a wedged one.
                state.last_frame_at[str(turn_id)] = time.monotonic()
                # Measured from `turn_start` when the backend told us when
                # the turn began, and from `loop_start` when it did not: a
                # recorded fact must not depend on an optional frame.
                ceiling_base = loop_start if turn_start is None else turn_start
                if (
                    not ceiling_crossed
                    and asyncio.get_running_loop().time() >= ceiling_base + ceiling_s
                ):
                    ceiling_crossed = True
                    rec["ceiling_crossed_s"] = round(
                        asyncio.get_running_loop().time() - ceiling_base
                    )
                    logger.warning(
                        "turn %s is past its %ss ceiling and still going — "
                        "recorded, not ended (topic %s)",
                        turn_id,
                        round(ceiling_s),
                        topic_id,
                    )
                if kind == "prompt_delivered":
                    # The transport accepted the write. Stamp the durable
                    # registry NOW: if this process dies a moment later, the
                    # sweep reads a fact instead of guessing from side
                    # effects that may not exist yet.
                    await effects.stamp_delivery(turn_id)
                    state.delivered.add(str(turn_id))
                    # The turn starts HERE, so the ceiling does too. Both
                    # clocks finally have a real base, and whichever comes
                    # first wins: the fuse still asks "did anything ever
                    # start" from `loop_start`, which is what makes the setup
                    # window its business, and the ceiling now asks "has this
                    # run too long" from the moment there was something to
                    # run.
                    turn_start = asyncio.get_running_loop().time()
                    continue
                if kind == "turn_ceiling":
                    ceiling_s = float(frame.get("seconds", policy.timeout))
                    # `topic_work()` reads this so `cheese_status` reports the
                    # backend's REAL ceiling, not the generic outer default.
                    rec["ceiling_s"] = ceiling_s
                    # The frame's only remaining job here: it is emitted before
                    # the container is touched, so the generic default that
                    # truncated the fuse (`min(first_output_fuse_s,
                    # policy.timeout)` above) was standing in for a ceiling
                    # nobody had declared. Now one is declared, the fuse gets
                    # its own full length back. The ceiling itself is recorded
                    # against `turn_start`, never scheduled.
                    if fuse_deadline is not None:
                        fuse_deadline = loop_start + max(0.0, first_output_fuse_s)
                        turn_deadline.reschedule(fuse_deadline)
                    continue
                if not lifecycle["started"] and not lifecycle["session_owned"]:
                    await publish(
                        channel,
                        {"type": "turn_started", "turn_id": str(turn_id)},
                    )
                    lifecycle["started"] = True
                if kind == "waiting":
                    rec["status"] = "waiting"
                    rec["detail"] = "Cloud machine provisioning"
                if kind == "assistant_block" and rec["first_output_s"] is None:
                    rec["first_output_s"] = round(time.monotonic() - t0, 2)
                    # The model spoke: the substrate is up, so hand the turn
                    # its real ceiling and retire the cold-start fuse.
                    if fuse_deadline is not None:
                        fuse_deadline = None
                        turn_deadline.reschedule(None)
                if kind == "error":
                    rec["status"] = "error"
                    rec["detail"] = str(frame.get("message", ""))[:200]
                    if frame.get("code") in HOST_SCOPED_CODES:
                        # The chat layer already recorded it against the
                        # machine and may have moved the topic; don't undo
                        # that below just because the stream ended cleanly.
                        host_failed = True
                        state.host_failed_topics.add(str(topic_id))
                await publish(channel, frame)
        if rec["status"] == "running":
            rec["status"] = "done"
        rec["duration_s"] = round(time.monotonic() - t0, 1)
        if (
            rec["status"] == "done"
            and rec["first_output_s"] is None
            and summon
            and not is_resume
            and content.strip()
        ):
            # A summoned turn that published nothing. Usually 芝士 simply had
            # nothing to say; sometimes its platform tools were gone and it
            # answered into a terminal nobody reads (observed 2026-09-13/14:
            # `chat_send` returning `No such tool available` while the
            # session reported ready). Only this case pays for the question,
            # and only a confirmed reconnect re-delivers the message.
            await effects.recover_silent_turn(topic_id, content, continuation_id)
        if not host_failed and str(topic_id) in state.host_failed_topics:
            state.host_failed_topics.discard(str(topic_id))
            # Streaming a turn to its end is the machine working. That breaks
            # the failure streak and lifts any quarantine (#186) — the way a
            # machine gets back into rotation without anyone clearing it.
            # Only reached when this process actually saw the machine fail:
            # there is nothing to clear otherwise, and paying a DB round-trip
            # on every good turn would delay the turn's release (see
            # `_host_failed_topics`).
            await effects.record_host_success(topic_id)
        logger.info(
            "turn done: status=%s tools=%s first_output=%ss duration=%ss",
            rec["status"],
            rec["tools"],
            rec["first_output_s"],
            rec["duration_s"],
        )
    except asyncio.CancelledError:
        # Killed from outside: `sweep_orphans` tearing down a wedged turn, or
        # the process shutting down. CancelledError is a BaseException, so
        # without this clause it escapes every handler below and the record
        # keeps saying `running` for as long as the process lives — and that
        # record is what `GET /topics` (`running`) and `/topics/{id}/status`
        # (`turn`) serve. Killing a turn while still reporting it alive is
        # the same lie the sweep exists to end, so the teardown has to close
        # the books here.
        rec["status"] = "cancelled"
        rec["duration_s"] = round(time.monotonic() - t0, 1)
        rec["detail"] = rec.get("detail") or "轮次被强制结束"
        logger.warning(
            "turn %s cancelled for topic %s after %ss",
            turn_id,
            topic_id,
            rec["duration_s"],
        )
        # Surface the request failure before `_run` publishes the explicit
        # turn_finished marker that retires this turn's broker state.
        # Publishing never suspends (it is queue writes only), so it is safe
        # on an already-cancelled task.
        await publish(
            channel,
            error_frame(say("turnForceStopped"), type="error", persisted=False),
        )
        # Ends the stream, for the reason spelled out on the timeout path.
        # `turn_finished` does not cover this: `_run` publishes it only when
        # `lifecycle["started"]` is set, and a turn the sweep tears down
        # before its first frame never announced itself, so a subscriber
        # would be left reading until its own timeout.
        await publish(channel, {"type": "done"})
        # The on-disk registry entry is deliberately left alone: whoever
        # cancelled us owns it (the sweep already claimed it; a shutdown
        # wants startup to find and resume it).
        clear_context("turn", "topic")
        raise
    except TimeoutError:
        rec["status"] = "timeout"
        rec["duration_s"] = round(time.monotonic() - t0, 1)
        # The actual ceiling this turn ran against — `topic_work()` reads
        # the same `ceiling_s or policy.timeout` fallback for `cheese
        # status` (see its docstring above); a backend that emitted a
        # `turn_ceiling` frame may have raised this well above
        # `policy.timeout`, so logging the base default here would be
        # misleading about what actually elapsed before the cut.
        # Two different failures share this handler, and telling them apart is
        # the whole point of the cold-start fuse — it decides which message
        # the room gets. "Ran a long time and wedged" is a turn problem, and
        # its work so far is on disk. "Never produced a token" is an
        # ENVIRONMENT problem (no container, no disk, no model connection):
        # nothing ran, so saying 已完成的改动都在 there would be a lie.
        # Neither is retried — a person picks it up (a wedged turn re-runs
        # into the same dead machine, and an environment that never came up
        # needs someone to look).
        # Only the cold-start fuse raises this now: the ceiling is recorded
        # rather than scheduled, so a turn that produced output has no
        # deadline left to hit. Kept as a check rather than assumed, so a
        # future deadline added above cannot silently borrow the fuse's
        # wording.
        never_started = bool(
            rec["first_output_s"] is None and policy.first_output_timeout
        )
        if not never_started:
            raise
        # Meta for the posted event: only the credential-expired case carries a
        # platform_error classification the frontend can render; the two generic
        # branches stay a plain system event, exactly as before.
        fuse_meta: dict | None = None
        # 平台提示统一契约的 meta，给「一个字没输出」和「超时」这两条用。
        # 凭据已过期那条走 `fuse_meta`（它带 code）。
        timeout_meta: dict | None = None
        if never_started and credential_expired:
            # #388 缺陷一: the backend KNEW the credential was dead. Say so —
            # the guessing message ("容器/磁盘/网络") is the one that cost four
            # people ten hours when the true reason was already known.
            logger.error(
                "turn %s never produced output for topic %s and its model "
                "credential is known-expired; fast-failing as a subscription "
                "credential failure (tools=%s)",
                turn_id,
                topic_id,
                rec["tools"],
            )
            rec["detail"] = "subscription credential expired"
            text = SUBSCRIPTION_CREDENTIAL_EXPIRED.content
            fuse_meta = SUBSCRIPTION_CREDENTIAL_EXPIRED.meta
        else:
            logger.error(
                "turn %s produced no output within %ss for topic %s; "
                "treating as a substrate failure (tools=%s)",
                turn_id,
                round(policy.first_output_timeout),
                topic_id,
                rec["tools"],
            )
            rec["detail"] = "no first output"
            # 平台提示统一契约: 房间里一行，「常见原因」那一串进 meta.detail。
            text = say("noFirstOutput", seconds=round(policy.first_output_timeout))
            timeout_meta = notice(
                EVENT_TURN_TIMEOUT,
                severity=SEVERITY_WARN,
                # 工作电脑没起来，平台不再自动重试 —— 要有人看一眼。
                who=WHO_HUMAN,
                detail=say(
                    "noFirstOutputDetail",
                    seconds=round(policy.first_output_timeout),
                ),
                detail_label=say("labelReason"),
                retryable=True,
            )
        block = None
        try:
            # `fuse_meta` (订阅凭据已过期) 优先：它带 code，下面的 error 帧
            # 认这个字段。其余两条走 `timeout_meta`。
            block = await effects.post_system_event(
                topic_id, text, turn_id, meta=fuse_meta or timeout_meta
            )
        except Exception:  # noqa: BLE001 — best effort
            logger.exception("failed to persist timeout event")
        if block is not None:
            await publish(channel, {"type": "event_block", "block": block})
        frame = error_frame(text, type="error", persisted=block is not None)
        if fuse_meta is not None:
            frame["code"] = SUBSCRIPTION_CREDENTIAL_EXPIRED.code
        await publish(channel, frame)
        # End the stream. `chat` publishes `done` on the paths it owns, and
        # this one cut its generator off mid-flight, so without this nothing
        # does: a subscriber waiting for the turn to end instead waits out
        # its own read timeout, which is how a squeezed ceiling became a
        # 300-second hang in CI rather than a fast failure. `turn_finished`
        # is no substitute, being published only for a turn that got far
        # enough to announce itself, which a turn cut down during setup
        # never did.
        await publish(channel, {"type": "done"})
        # No auto-retry, whatever the timeout was. A credential-dead turn
        # only burns the fuse again against the same expired credential
        # (#388's "对自己的失败没有记忆") and self-heals on the next human
        # summon once the host re-auths; a wedged one re-enters the machine
        # that just died; an environment that never came up needs a person.
        # All three now wait for someone to look — the event above said so.
    except BaseError as exc:
        rec["status"] = "error"
        rec["detail"] = exc.message
        rec["duration_s"] = round(time.monotonic() - t0, 1)
        await publish(channel, error_frame(exc.message, type="error"))
        # Ends the stream, for the reason spelled out on the timeout path.
        await publish(channel, {"type": "done"})
    except Exception as exc:  # noqa: BLE001 — surface runtime failures (spec H4)
        rec["status"] = "crashed"
        rec["duration_s"] = round(time.monotonic() - t0, 1)
        logger.exception("turn %s failed for topic %s", turn_id, topic_id)
        # The failure goes into the 现场 timeline as a persisted system event
        # (scrolls with the flow, survives reload) — not just a transient
        # banner. No invented cause: the log has the real traceback.
        platform_failure = classify_platform_failure(exc)
        if platform_failure is not None:
            text = platform_failure.content
            event_meta = platform_failure.meta
        else:
            # 平台提示统一契约: 一行给房间，别的收进 detail。真正的 traceback
            # 只进日志（这里连异常文本都不外发是刻意的 —— 见上面那段注释）。
            # 平台认不出来的失败当作缺陷信号，不当瞬时故障 —— 重试只会把同一个
            # bug 再触发一遍（2026-09-04 一个 NotFoundError 被连着自动重跑，
            # 把一个正在进行的 hackathon 房间刷了屏）。发一次，交给人。
            text = say("turnCrashed")
            event_meta = notice(
                EVENT_TURN_FAILED,
                severity=SEVERITY_ERROR,
                who=WHO_HUMAN,
                detail=say("turnCrashedDetail"),
                detail_label=say("labelDetails"),
                retryable=True,
            )
        block = None
        try:
            block = await effects.post_system_event(
                topic_id, text, turn_id, meta=event_meta
            )
        except Exception:  # noqa: BLE001 — best effort, never mask the error
            logger.exception("failed to persist turn-failure event")
        if block is not None:
            await publish(channel, {"type": "event_block", "block": block})
        frame = error_frame(text, type="error", persisted=block is not None)
        if platform_failure is not None:
            frame["code"] = platform_failure.code
        await publish(channel, frame)
        # Ends the stream, for the reason spelled out on the timeout path.
        await publish(channel, {"type": "done"})
        # An unnamed failure is a bug signal, not a transience signal, so the
        # platform does not repeat it — the event above already handed the
        # topic to a person (#574, and the 2026-09-04 room flood). A recovery
        # that survives a deploy is a different thing entirely and still runs:
        # recover/replay/adopt carry a live turn across a restart untouched.
        if platform_failure is not None and platform_failure.host_scoped:
            # The machine, not the turn, is the suspect. Account for it
            # against the device and, once it has failed twice in a row,
            # say so in the room by name. No retry and no other machine:
            # the topic stays pinned where it failed until a person has
            # looked (host_failure.py says why the swap it used to do is
            # gone).
            host_failed = True
            state.host_failed_topics.add(str(topic_id))
            await effects.handle_host_failure(topic_id, turn_id, platform_failure)
    # A session that adopted this exact turn owns its interval's ending.
    # Returning after input injection is not the native work ending.
    session_owns_ending = (
        rec["status"] == "done"
        and lifecycle["session_owned"]
        and effects.session_took_over(topic_id, turn_id)
    )
    if not session_owns_ending:
        try:
            await close_turns(sessions, [turn_id])
        except Exception:  # noqa: BLE001 — the turn already finished
            logger.exception("could not close the interval for turn %s", turn_id)
    clear_context("turn", "topic")
