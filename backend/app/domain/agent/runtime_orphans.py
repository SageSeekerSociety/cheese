"""Orphan inspection and recovery for the process-scoped work runner."""

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime

from sqlalchemy import func, select

from app.core.background import hold
from app.core.sentences import say
from app.domain.addressing import Addressed
from app.domain.agent import death_evidence, turn_inputs
from app.domain.agent.pending_messages import resume_messages
from app.domain.agent.queries import instance_of_handle
from app.domain.agent.repositories import TurnRecord
from app.domain.agent.turn_ledger import utcnow
from app.domain.block.models import Block
from app.domain.notice_meta import EVENT_TURN_TIMEOUT, SEVERITY_WARN, WHO_HUMAN, notice
from app.domain.run_record.models import RunRecord
from app.domain.topic_membership.services import addressable_seat

logger = logging.getLogger("cheesex.runtime")


async def _wedged_turns(
    self,
    open_turns: dict[uuid.UUID, TurnRecord],
    last_activity: (
        Callable[[set[uuid.UUID]], Awaitable[dict[uuid.UUID, datetime]]] | None
    ),
    silence_s: float,
    now: datetime,
) -> set[uuid.UUID]:
    """Of the turns this process is watching, which have gone quiet on BOTH
    signals? Startup passes no `last_activity` — there is nothing to judge
    then, so the probe is skipped entirely.

    Watching, not running: a self-started turn has no coroutine and so is
    never in `_live`, but it is the one kind of turn that CANNOT be caught by
    the other branch either. Its screen is the room's own and answers a
    liveness probe long after the work behind it stopped, so `_adopted` keeps
    saying yes and its interval would stay open forever. What it does have is
    a frame stamp, which is exactly what silence is judged on.
    """
    candidates = {
        tid
        for tid in open_turns
        if str(tid) in self._live or str(tid) in self._last_frame_at
    }
    if not candidates or last_activity is None:
        return set()
    try:
        blocks_at = await last_activity(
            {open_turns[tid].topic_id for tid in candidates}
        )
    except Exception:  # a failed probe must not cancel turns
        logger.exception("orphan sweep: last-activity probe failed")
        return set()
    mono = time.monotonic()
    wedged: set[uuid.UUID] = set()
    for tid in candidates:
        record = open_turns[tid]
        # Wall-clock age of the newest Block in the topic...
        last_block = blocks_at.get(record.topic_id)
        block_quiet_s = (
            (now - last_block).total_seconds()
            if last_block is not None
            else record.age_s(now)
        )
        # ...versus the newest frame this process published for this turn.
        # A turn we are running always has an entry (set at start), so a
        # missing one means the bookkeeping is off — treat it as fresh and
        # let the not-in-`_live` branch handle it instead of guessing.
        frame_at = self._last_frame_at.get(str(tid))
        if frame_at is None:
            continue
        frame_quiet_s = mono - frame_at
        if min(block_quiet_s, frame_quiet_s) > silence_s:
            wedged.add(tid)
            logger.warning(
                "turn %s is wedged: no block for %ss, no frame for %ss",
                tid,
                round(block_quiet_s),
                round(frame_quiet_s),
            )
    return wedged


async def _last_progress(
    session_factory, turn_ids: set[uuid.UUID]
) -> dict[uuid.UUID, datetime]:
    """The newest thing each turn put on record: a block or a run record
    carrying its id. A turn with neither is absent."""
    newest: dict[uuid.UUID, datetime] = {}
    async with session_factory() as session:
        for model in (Block, RunRecord):
            rows = await session.execute(
                select(model.turn_id, func.max(model.created_at))
                .where(model.turn_id.in_(turn_ids))
                .group_by(model.turn_id)
            )
            for turn_id, at in rows:
                if turn_id not in newest or at > newest[turn_id]:
                    newest[turn_id] = at
    return newest


async def _silent_unknown_turns(
    self,
    chat_service,
    open_turns: dict[uuid.UUID, TurnRecord],
    silence_s: float,
    now: datetime,
) -> set[uuid.UUID]:
    """Of the delivered turns no process holds and nothing has pronounced
    dead (`death_evidence.unknown_row`), which have put nothing on record for
    `silence_s`? Those are wedged too.

    Unknown is not dead: a machine merely out of reach may still be working,
    so such a row is never closed for being unreachable. But a turn whose
    machine nobody can reach and which has said nothing for as long as a
    healthy turn can be quiet is not one anybody is still waiting on — the
    person is, and every message they send the seat queues behind it
    (`pending_messages`: ``turn_open_in_db``). On 2026-10-11 one such row held
    a thread for nineteen days: its sandbox was released, nothing was left to
    report its end, and four follow-ups from the person it was answering never
    became a turn.

    Silence is read from what the turn itself put on record — its blocks and
    run records — not from the room, because a person asking 「你卡了？」 is
    not the turn making progress. The one signal this cannot see is a frame,
    and frames only reach a process that holds the session; a row in this set
    is by definition one no process holds.
    """
    candidates = {
        tid: record
        for tid, record in open_turns.items()
        if record.delivered
        and str(tid) not in self._live
        and str(tid) not in self._last_frame_at
        and not self._adopted(chat_service, record, set())
        and death_evidence.unknown_row(chat_service, record)
    }
    if not candidates:
        return set()
    try:
        progress = await _last_progress(chat_service.session_factory, set(candidates))
    except Exception:  # a failed probe must not end turns
        logger.exception("orphan sweep: turn progress probe failed")
        return set()
    silent: set[uuid.UUID] = set()
    for tid, record in candidates.items():
        latest = max(
            [at for at in (progress.get(tid), record.delivered_at) if at is not None]
        )
        quiet_s = (now - latest).total_seconds()
        if quiet_s > silence_s:
            silent.add(tid)
            logger.warning(
                "turn %s is wedged: no session holds it and it put nothing on"
                " record for %ss",
                tid,
                round(quiet_s),
            )
    return silent


def _cancel_wedged(self, turn_id: str, topic_id: uuid.UUID) -> None:
    """Tear down a turn whose task is alive but producing nothing. Its own
    `finally` does the rest of the cleanup (gate release, `_live` removal)
    once the cancellation lands at its next await point."""
    task = self._live.get(turn_id)
    if task is None or task.done():
        return
    task.cancel()
    logger.warning("cancelled wedged turn %s on topic %s", turn_id, topic_id)


async def resume_orphans(self, chat_service) -> int:
    """The sweep a process runs as it takes the work over. Every open
    interval then belongs to a previous owner, which has stopped listening
    and let go of its turns, or to a turn this process started since, which
    is in `_live` before its interval is written — which makes this exactly
    `sweep_orphans` with the young-entry guard switched off."""
    return await self.sweep_orphans(chat_service, min_age_s=0.0)


async def resume_lost_messages(self, chat_service, **scope) -> int:
    """Start the turns a previous owner accepted a message for and never
    began. Returns how many turns it started.

    Between a message being stored and its turn opening, the turn exists
    only in the memory of the process that accepted it: waiting for this
    process to take over, or queued behind the project's other turns — the
    room was even told 「前面的轮次结束就自动开跑，不用重发」. A process that
    goes away drops that wait, and nothing else would ever start the turn.

    What counts: a message that named an agent, has not been read into any
    prompt, is recent, and has nothing about its turn in the room — no
    interval, no reply, no refusal. The agent it named must have no turn
    running, or the message is that turn's to pick up. One turn per agent
    in a room: it carries every message waiting for that agent, the way any
    turn does, and teammates in one room run side by side, so another
    agent's turn neither picks this message up nor holds it back.
    """

    return await resume_messages(self, chat_service, **scope)


async def sweep_orphans(
    self,
    chat_service,
    *,
    min_age_s: float | None = None,
    last_activity: (
        Callable[[set[uuid.UUID]], Awaitable[dict[uuid.UUID, datetime]]] | None
    ) = None,
    silence_s: float | None = None,
) -> int:
    """Claim every turn whose interval is open but not actually progressing,
    and make its fate VISIBLE in the topic. Returns how many were auto-resumed.

    Why this is not startup-only (the 101/173-minute incident, 2026-08-11):
    a turn dying does not imply the platform restarted. A container recreate,
    an OOM-killed child, a sandbox image swap — each kills a turn while the
    process lives happily on. Nothing then ever re-reads the open intervals,
    so one sits there and the topic keeps reporting `active` with a last block
    that is a command which never returned. That is indistinguishable, to a
    human reading the platform, from a slow test run.

    A turn is dead in one of two ways, and BOTH must be caught — the second
    is what let three topics lie silent for 8 hours on 2026-08-11 while this
    process was up the whole time:

    1. Not in `_live` — a previous generation of the process started it and
       died. An open interval outlives the process; `_live` does not.
    2. In `_live` but SILENT — the task is still parked in the event loop,
       but nothing is coming out of it. What died is the thing it was driving
       (the sandbox container, the provider stream), not the task. The wall
       clock ceiling does not save us here: a backend that signalled a large
       `turn_ceiling` can legitimately hold the deadline open for hours.

    Silence is judged on two signals, taking the more recent — a turn is only
    dead if BOTH are cold. `last_activity` is the topic's newest Block (the
    signal a human can verify from the UI, and the one that survives a wrong
    `_live`); `_last_frame_at` is the newest frame this process published,
    which also counts tool calls — those persist no Block, so a long
    tool-only stretch is alive but invisible to the DB alone. Erring toward
    "still alive" is deliberate: resuming a live turn is worse than noticing
    a dead one late.

    Not every turn missing from `_live` is dead, and that is the whole of
    what this sweep learned to stop doing. A backend restart kills the
    coroutine WAITING on a turn; the claude out in the execution environment
    keeps working, and `recover_sessions` finds its screen again on the
    way up. Such a turn is adopted — left running, its interval left open,
    and closed by the Stop that screen eventually sends, exactly as if
    nothing had happened. Nothing is said, because nothing broke.

    What is left needs a remedy:

    - WEDGED (in `_live`, silent): the thing the task drove — the sandbox
      container, the provider stream — is what died, taking its claude with
      it. Cancel it and hand the topic to a person: re-running a turn only
      re-enters the machine that just died under it, so the platform does
      not do it on its own. A person looks, then re-@s 芝士, which reconnects
      to the `--resume`d session that still holds the conversation.
    - STRANDED (no screen, or a screen that never heard the prompt): the
      task never reached anyone. One re-send per agent in the topic, and it is the
      ORIGINAL text (the pending-message mechanism re-hands it verbatim),
      never a "接着干" nudge a task-less claude cannot act on. Too old, or
      itself a re-send, and the topic is handed to a person instead.
    - DELIVERED but no screen answers: the prompt got through and the
      container it got through to is gone. This one is silent — collect
      whatever the dead screen parked and stop there. NOT re-prompted, even
      though the saved session would make it possible: a device's screens
      come back when the device dials in, which can be minutes after this
      sweep runs, so "no screen" at startup routinely means "not yet".
      Re-prompting on that would re-send every device topic on every deploy.

    A turn the platform does not re-run is a turn someone has to pick up by
    hand, and they can only do that if the topic says so — silence is the
    failure mode, not the loud recovery."""
    open_turns = await turn_inputs.open_turns(chat_service.session_factory)
    if not open_turns:
        return 0
    if min_age_s is None:
        min_age_s = self.SWEEP_MIN_AGE_S
    if silence_s is None:
        silence_s = float(self.SILENT_TURN_S)
    now = utcnow()
    old_enough = {
        tid: record
        for tid, record in open_turns.items()
        if record.age_s(now) >= min_age_s
        and chat_service.replaying(record.topic_id) is None
    }
    wedged = await self._wedged_turns(old_enough, last_activity, silence_s, now)
    # Silence is judged on a clock, never at takeover: startup passes no
    # `last_activity`, and a device's screens may dial in minutes after it.
    silent = (
        await _silent_unknown_turns(self, chat_service, old_enough, silence_s, now)
        if last_activity is not None
        else set()
    )
    wedged |= silent
    # A row the process cannot judge is not a dead row (FB-56 legacy③): it
    # stays open (`death_evidence.unknown_row`), unless no process holds it
    # and it has been silent past the horizon (`_silent_unknown_turns`).
    orphans = {
        tid: record
        for tid, record in old_enough.items()
        if tid in silent
        or (
            (str(tid) not in self._live or tid in wedged)
            and not self._adopted(chat_service, record, wedged)
            and not death_evidence.unknown_row(chat_service, record)
        )
    }
    if not orphans:
        return 0

    # Claim them by closing their intervals: a turn this sweep is deciding
    # the fate of must not be decided again by the next one. Only these ids
    # — a turn that opened while the activity probe awaited is still open,
    # and a running turn no sweep can see is how the next death goes silent
    # again, which is the whole bug.
    claimed = await turn_inputs.close_dead_turns(chat_service.session_factory, orphans)
    # Startup and an overdue periodic tick can see the same open intervals.
    # Only the sweep whose UPDATE closed a turn may arrange its recovery.
    orphans = {tid: record for tid, record in orphans.items() if tid in claimed}
    for turn_id in orphans:
        # A turn with a coroutine gets its marks dropped by that coroutine's
        # own `finally`; one without (a self-started turn, or anything left
        # by a dead process generation) has nobody to do it, and a stale
        # frame stamp would keep offering the same corpse to every sweep.
        if str(turn_id) not in self._live:
            self._forget_turn(turn_id)
    remedied = 0
    # --- wedged turns: their claude died WITH whatever they were driving.
    # Cancel to release the topic lock, tell the room once, and stop —
    # re-running only re-enters the machine that just died, so a person
    # picks it up and re-@s 芝士 when the environment is back.
    wedged_topics: set[uuid.UUID] = set()
    for turn_id, record in orphans.items():
        if turn_id not in wedged:
            continue
        topic_id = record.topic_id
        age_s = record.age_s(now)
        # A wedged turn still owns the topic lock. Cancelling is not tidiness
        # — even a turn nobody will re-run must let the next human message
        # through.
        self._cancel_wedged(str(turn_id), topic_id)
        # One event per topic: a queued turn parked behind a wedged one looks
        # wedged too, and a second notice for the same room is only noise.
        if topic_id in wedged_topics:
            continue
        wedged_topics.add(topic_id)
        await self._post_orphan_event(
            chat_service,
            topic_id,
            say("turnWedged", minutes=round(age_s / 60)),
            # The turn this notice is about, so the run-record page can answer
            # "did this turn fail" from the event alone (turns.py).
            turn_id=turn_id,
            meta=notice(
                EVENT_TURN_TIMEOUT,
                severity=SEVERITY_WARN,
                # 平台不再自动重试 —— 要有人看一眼、再 @ 它。
                who=WHO_HUMAN,
                detail=say("turnWedgedDetail"),
                detail_label=say("labelDetails"),
                retryable=True,
            ),
        )
        logger.info(
            "orphan turn %s cancelled as wedged (age=%ss); no auto-retry",
            turn_id,
            round(age_s),
        )
        # No increment: the return counts remedial RE-SENDS scheduled, and a
        # wedged turn is cancelled and announced but never re-run — the same
        # zero the stale-wedged drop always returned.
    # --- what is left after adoption: no screen answers for this topic, or
    # one does and never heard the prompt. Gathered per TOPIC, because the
    # dispatch record and the room's notice are the room's; the re-sends
    # inside are per seat (`_settle_restart_orphans`).
    # 每一个孤儿话题都要走一趟下面那个函数，**卡死的那些也要** —— 它是读平台侧
    # 执行记录的地方，而机器整台死掉正是那份记录存在的旗舰场景（6.5「突然损
    # 坏」）：那一轮会在 `SILENT_TURN_S` 之后被判卡死，如果卡死的话题就此不再往下
    # 走，悬着的调用既不会被说出来也不会被结清，房间里只剩一条「平台不会自动重
    # 试……@ 芝士，它会从断点接着做」，一个字没提有一次写可能已经落地一半。
    # 卡死的轮次自己不进 `entries`：它们已经在上面各自了结过，这里给的是一个话题
    # 里**还没了结**的那些，可以是空的。
    #
    # `since` 划出这次要收拾的那段时间：一个话题里最早的那个孤儿轮次什么时候开
    # 的。执行记录按房间存，而这一趟问的是「**这几轮**里有什么悬着」——上一轮留
    # 下的空行不该顶掉这一轮一次合法的重发（见 `dispatch_log.unsettled`）。卡死的
    # 轮次也算进来：机器死在它手上，它正是那些悬着的调用的来源。
    by_topic: dict[uuid.UUID, list[TurnRecord]] = {}
    since: dict[uuid.UUID, datetime] = {}
    for turn_id, record in orphans.items():
        entries = by_topic.setdefault(record.topic_id, [])
        if turn_id not in wedged:
            entries.append(record)
        earliest = since.get(record.topic_id)
        if earliest is None or record.started_at < earliest:
            since[record.topic_id] = record.started_at
    for topic_id, entries in by_topic.items():
        remedied += await self._settle_restart_orphans(
            chat_service,
            topic_id,
            entries,
            now,
            since=since[topic_id],
            # A topic the wedged branch already remedied gets no second
            # action — its restart orphans are folded in, loudly.
            allow_actions=topic_id not in wedged_topics,
        )
    return remedied


async def _post_orphan_event(
    self,
    chat_service,
    topic_id: uuid.UUID,
    text: str,
    turn_id: uuid.UUID | None = None,
    meta: dict | None = None,
) -> None:
    """Persist + broadcast an orphan verdict. Best-effort by design: for a
    resumed orphan the resume matters more than the notice, and for a dropped
    one there is nothing left to fail into.

    ``turn_id`` is the turn the notice is about, when the sweep knows one: the
    run-record page counts a turn as failed only by an event bearing its id
    (`run_record/turns.py`). Notices about the topic as a whole pass none.

    ``meta`` is the 平台提示统一契约 payload — chiefly `who`, which is what
    lets someone decide "does this need me?" without reading the sentence."""
    try:
        block = await chat_service.post_system_event(
            topic_id, text, turn_id=turn_id, meta=meta
        )
        if block is not None:
            await self._broker.publish(
                str(topic_id), {"type": "event_block", "block": block}
            )
    except Exception:  # a notice must never break the sweep
        logger.exception("orphan event failed for %s", topic_id)


async def _recover_silent_turn(
    self,
    chat_service,
    topic_id: uuid.UUID,
    content: str,
    continuation_id: uuid.UUID | None,
    *,
    address_agent: Callable[[str | None], Addressed],
) -> None:
    """Put the room's tools back if they were gone, then re-deliver."""
    try:
        recovered = await chat_service.recover_native_tools(topic_id)
    except Exception:  # recovery must never fail a finished turn
        logger.exception("tool recovery check failed for topic %s", topic_id)
        return
    if not recovered:
        return
    await self._post_orphan_event(
        chat_service,
        topic_id,
        self._TOOLS_RECOVERED_TEXT,
        # Topic-wide: the tools came back for the room, not for one turn.
        meta=self._TOOLS_RECOVERED_META,
    )
    # 重新投递，不是新起一轮：收件人还是上一条消息点的那个席位，平台只是把没送
    # 到的那一次送完（I12）。席位没了就没有收件人，这一次也就不再重发。
    self.submit(
        chat_service,
        topic_id,
        author="system",
        content=content,
        addressed=address_agent(
            await addressable_seat(chat_service.session_factory, topic_id)
        ),
        is_resume=True,
        resume_reason=self.TOOLS_REASON,
        continuation_id=continuation_id,
    )


def _schedule_resend(
    self,
    chat_service,
    topic_id: uuid.UUID,
    after_s: float,
    content: str,
    *,
    continuation_id: uuid.UUID | None = None,
    agent_handle: str | None = None,
    address_agent: Callable[[str | None], Addressed],
):
    """One bounded re-delivery of a prompt with NO evidence of arrival.

    The prompt this turn actually runs with is the ORIGINAL text — an
    interrupted turn never stamps its inputs consumed, so the pending-
    message mechanism re-hands them verbatim; ``content`` is only the
    fallback for the rare topic with nothing pending. It is a re-delivery,
    not a "pick up where you left off" nudge, which would be meaningless to
    a claude that never heard the task. `is_resume=True` keeps it from ever
    chaining further automatic turns, and the inherited continuation keeps
    any side effect that somehow DID land from being repeated.

    ``agent_handle`` is whose conversation the interrupted turn ran in; the
    re-send goes back to that agent. Without it the room decides, as for
    any turn nobody named an agent for."""

    async def _later() -> None:

        await asyncio.sleep(after_s)
        recipient = (
            None
            if agent_handle is None
            else await instance_of_handle(
                chat_service.session_factory, topic_id, agent_handle
            )
        )
        self.submit(
            chat_service,
            topic_id,
            author="system",
            content=content,
            addressed=address_agent(
                await addressable_seat(chat_service.session_factory, topic_id)
            ),
            is_resume=True,
            resume_reason=self.RESEND_REASON,
            continuation_id=continuation_id,
            recipient_instance_id=recipient,
        )

    hold(
        asyncio.create_task(_later()),
        self._tasks,
        name=f"orphan-resend-{topic_id}",
    )
    logger.info("scheduled orphan re-send for topic %s in %.0fs", topic_id, after_s)
