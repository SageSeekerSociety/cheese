"""Read a runner that speaks the lifecycle protocol (`lifecycle.py`).

One read of the runner answers three things, and each is taken its own way:

- **The events** — the session's facts, in order. Each is handed on as a
  ``Report`` (`agent/reads.py`); whoever reads the session applies it and its
  effects in one transaction (``session_state.take``) before it lets the reading
  go on, and only then is it acknowledged to the runner, on the next read. A
  reader that fails on one leaves it, and everything after it, unacknowledged:
  the runner hands them over again, and an event already applied changes
  nothing the second time.
- **The records** — what the session wrote, the data plane. They are kept as
  its raw record (``records``) and shown in the room (``consume``), each before
  the events the runner wrote after it (an event names how far the journal had
  got, ``records``); neither decides anything, and neither holds the events
  up. A page the raw record
  refuses is read again on the next pass; a record the room keeps refusing is
  stepped over after ``REFUSED_TIMES`` over ``REFUSED_OVER_S``, and one older
  than ``STALE_S`` is not shown at all — the room went on without it.
- **The session as it stands** — its worker status, what it came up with, its
  background tasks. Kept here for whoever asks (``state``); a change in the
  tasks is told (``controls``).

Nothing is kept on this side between processes: the runner keeps the events
until they are acknowledged and remembers how far the records were taken, so a
backend that starts reading a session it never read before starts where the
last one stopped.
"""

import asyncio
import dataclasses
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.driven import lifecycle
from app.domain.agent.harness.driven.subscription import (
    REFUSED_OVER_S,
    REFUSED_TIMES,
    STALE_S,
    Records,
)
from app.domain.agent.platform_failures import (
    PROMPT_UNDELIVERED_CODE,
    PROMPT_UNDELIVERED_MESSAGE,
    TURN_TIMEOUT_CODE,
    TURN_TIMEOUT_MESSAGE,
    classify_session_start,
)
from app.domain.agent.reads import (
    InputDropped,
    InputRead,
    InputWritten,
    Report,
    SessionExited,
    SessionIdle,
    SessionRunning,
)
from app.domain.agent.service import AgentEvent, AgentResult, AgentUsage
from app.domain.delivery.input_identity import InputIdentity, InputReceipt

logger = logging.getLogger(__name__)

#: How the reading hands on what it read, in the session core's reading.
#: (work id, event, event id, unsolicited) — one record's events for the room.
Show = Callable[[uuid.UUID, AgentEvent, str, bool], Awaitable[None]]
#: One event the runner reported, with the work it is for when it names one.
Hand = Callable[[uuid.UUID | None, Report], Awaitable[None]]


@dataclass(frozen=True)
class Whose:
    """Whose session this reading reads, as its events are keyed and its
    inputs identified: the seat's ``SessionRef``, the agent acting in it, and
    the registration key its epochs are issued under."""

    seat: SessionRef
    acting: str
    conversation_id: uuid.UUID
    agent_handle: str
    harness: str
    label: str


def result_of(
    whose: Whose, result: dict, *, session_id: str | None
) -> tuple[AgentResult, str | None]:
    """How the work ended, in the room's vocabulary, and the failure's kind.

    The typed failure is the runner's; the words the room reads and the codes
    its policies key on are made here, once for every harness.
    """
    common = {
        "session_id": session_id,
        "agent_handle": whose.acting or None,
        "harness": whose.harness,
        "rate_limit": result.get("rate_limit")
        if isinstance(result.get("rate_limit"), dict)
        else None,
        "inputs_read": bool(result.get("inputs")),
    }
    failure = result.get("failure")
    if not isinstance(failure, dict):
        usage = result.get("usage")
        return (
            AgentResult(
                text=str(result.get("text") or ""),
                usage=AgentUsage(**usage) if isinstance(usage, dict) else None,
                **common,
            ),
            None,
        )
    kind = str(failure.get("kind") or lifecycle.ERROR)
    if kind == lifecycle.INTERRUPTED:
        # Somebody took the work away. That ends it; it is not a failure of
        # anything, and saying 「出错了」 would tell the room so.
        return AgentResult(text="", **common), kind
    if kind == lifecycle.STUCK:
        stuck = failure.get("stuck")
        message, code = (
            (TURN_TIMEOUT_MESSAGE, TURN_TIMEOUT_CODE)
            if stuck == lifecycle.NO_PROGRESS
            else (PROMPT_UNDELIVERED_MESSAGE, PROMPT_UNDELIVERED_CODE)
        )
        return (
            AgentResult(text=message, is_error=True, failure_code=code, **common),
            kind,
        )
    if kind in (lifecycle.EXITED_MIDWAY, lifecycle.READER_FAILED):
        # A session its runner could no longer read was ended by it: to the
        # room, its process exited in the middle of the work, as it did.
        said = str(failure.get("said") or "")
        if said:
            # Its process ended before it ever came up: a session that did
            # not start, and the room hears why, as it would had the start
            # itself failed.
            refused = classify_session_start(said, harness=whose.label)
            return (
                AgentResult(
                    text=refused.content,
                    is_error=True,
                    failure_code=refused.code,
                    log=said,
                    **common,
                ),
                kind,
            )
        return (
            AgentResult(
                text=f"{whose.label} session process exited", is_error=True, **common
            ),
            kind,
        )
    # The kind of an API failure rides in the text, where the room's
    # classifier reads it (`billing` is how a spent balance is told from a bad
    # key), and its status beside it.
    reason = str(failure.get("code") or kind)
    said = str(failure.get("detail") or "").strip()
    errors = [str(error) for error in failure.get("errors") or [] if str(error)]
    status = failure.get("status")
    return (
        AgentResult(
            text=f"{said}（{reason}）" if said else f"AI 服务拒绝了请求（{reason}）",
            is_error=True,
            errors=errors or [reason],
            api_error_status=status if isinstance(status, int) else None,
            **common,
        ),
        kind,
    )


def _uuid(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


class Reading:
    """The reading of one runner that speaks the lifecycle protocol."""

    def __init__(
        self,
        whose: Whose,
        call: Callable[[str, dict], Awaitable[dict]],
        *,
        epoch: Callable[[], int],
        assemble: Callable[[dict], list[AgentEvent]],
        show: Show,
        hand: Hand,
        records: Records | None,
        controls: Callable[[], Awaitable[None]],
    ):
        self.whose = whose
        self.call = call
        #: The epoch this process registered the runner under, as it stands:
        #: a registration this process makes again is still its own.
        self.epoch = epoch
        self.assemble = assemble
        self.show = show
        self.hand = hand
        self.records = records
        self.controls = controls
        self.lock = asyncio.Lock()
        #: The last event the platform applied, acknowledged on the next read.
        self.acked = 0
        #: How far the records were taken; None until this reading took any,
        #: which asks the runner to start where the last reader stopped.
        self.records_after: int | None = None
        self.refusals: dict[int, tuple[int, float]] = {}
        #: The session as the runner last said it stands.
        self.state: dict = {}
        self.heard: dict = {}
        self.live_mark: str | None = None
        self.heard_at: float | None = None
        #: The current process's ending, once it was applied.
        self.exited: SessionExited | None = None
        self.parked: asyncio.Future | None = None
        self.hurry = False

    def unpark(self) -> None:
        """Have a read the runner is holding answer at once instead."""
        self.hurry = True
        if self.parked is not None:
            self.parked.cancel()

    async def release(self) -> None:
        """Nothing is held on this side between reads."""

    def _params(self) -> dict:
        params: dict = {"epoch": self.epoch(), "ack": self.acked}
        if self.records_after is not None:
            params["records_after"] = self.records_after
        return params

    async def _read(self, wait: float) -> dict:
        params = self._params()
        if wait <= 0 or self.hurry:
            self.hurry = False
            return await self.call("read", params)
        self.parked = asyncio.ensure_future(
            self.call("read", {**params, "wait": wait, "live": self.live_mark})
        )
        try:
            return await self.parked
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise
            return await self.call("read", params)
        finally:
            self.parked = None

    async def drain(self, wait: float = 0.0) -> None:
        """One pass: read the runner, keep and show its records, hand on its
        events, and remember how the session stands."""
        if wait <= 0:
            self.unpark()
        async with self.lock:
            answer = await self._read(wait)
            self.heard_at = time.monotonic()
            if answer.get("worker_epoch") != self.epoch():
                # Registered since by somebody else: this reading is over.
                raise lifecycle.StaleEpoch(self.epoch(), answer.get("worker_epoch"))
            self.heard = {k: v for k, v in answer.items() if k in ("alive", "live")}
            if live := self.heard.get("live"):
                self.live_mark = live.get("mark")
            state = answer.get("state")
            moved = False
            if isinstance(state, dict):
                moved = state.get("tasks") != self.state.get("tasks")
                self.state = state
            rows = list(answer.get("records") or [])
            # Shown in the order the session did things: the records the
            # runner had journaled when it wrote an event come before it.
            showing = await self._archive(rows)
            for event in answer.get("events") or []:
                if showing:
                    showing = await self._show_through(rows, event.get("records"))
                report = self._report(event)
                if report is not None:
                    await self.hand(
                        report.fact.work_id
                        if isinstance(report.fact, SessionRunning | SessionIdle)
                        else None,
                        report,
                    )
                    if (
                        isinstance(report.fact, SessionExited)
                        and report.of_this_registration
                    ):
                        self.exited = report.fact
                self.acked = max(self.acked, int(event["seq"]))
                if self.exited is not None:
                    break
            if showing:
                await self._show_through(rows, None)
            if moved:
                await self.controls()
            if self.exited is not None:
                # What the runner still holds is acknowledged on the way out:
                # it lingers for exactly this.
                try:
                    await self.call("read", self._params())
                except Exception:  # it keeps them for the next runner either way
                    logger.info("could not acknowledge a session's last events")

    async def _archive(self, rows: list[dict]) -> bool:
        """Keep ``rows`` as the session's raw record; whether they may be
        shown now. The raw record never holds the session up: rows it refused
        are read again on the next pass, and shown then."""
        if not rows:
            return False
        if self.records is None:
            return True
        try:
            await self.records(self.whose.seat, rows)
        except Exception:
            logger.exception(
                "raw record refused journal rows %s-%s topic=%s agent=%s",
                rows[0]["sequence"],
                rows[-1]["sequence"],
                self.whose.seat.topic_id,
                self.whose.seat.agent_handle,
            )
            return False
        return True

    async def _show_through(self, rows: list[dict], through: int | None) -> bool:
        """Show the rows not shown yet, up to the journal sequence ``through``
        (all of them for None); whether the room took them, so the rest may
        follow. A record older than ``STALE_S`` is not shown: the room went on
        without it."""
        now = datetime.now(UTC)
        for row in rows:
            sequence = int(row["sequence"])
            if self.records_after is not None and sequence <= self.records_after:
                continue
            if through is not None and sequence > through:
                return True
            record = row.get("record")
            if (
                isinstance(record, dict)
                and (now - datetime.fromisoformat(row["at"])).total_seconds() < STALE_S
            ):
                try:
                    await self._show(record, sequence)
                except Exception:
                    if not self._given_up(sequence):
                        # Shown again on the next pass, from this record on;
                        # the events of this pass go on regardless.
                        logger.warning(
                            "the room refused journal record %s topic=%s agent=%s",
                            sequence,
                            self.whose.seat.topic_id,
                            self.whose.seat.agent_handle,
                            exc_info=True,
                        )
                        return False
                    logger.exception(
                        "stepped over journal record %s the room refused %d times "
                        "over %ds topic=%s agent=%s",
                        sequence,
                        REFUSED_TIMES,
                        REFUSED_OVER_S,
                        self.whose.seat.topic_id,
                        self.whose.seat.agent_handle,
                    )
            self.records_after = sequence
        return True

    def _given_up(self, sequence: int) -> bool:
        times, since = self.refusals.get(sequence, (0, time.monotonic()))
        self.refusals = {sequence: (times + 1, since)}
        return times + 1 >= REFUSED_TIMES and time.monotonic() - since >= REFUSED_OVER_S

    async def _show(self, record: dict, sequence: int) -> None:
        stamp = record.get("cheese") or {}
        work = _uuid(stamp.get("work_id"))
        if work is None:
            # Before the first input, or the platform's own work: the session
            # starting up says nothing the room has to hear.
            return
        for event in self.assemble(record):
            await self.show(
                work,
                event,
                getattr(event, "eid", None) or f"{self.whose.harness}:{sequence}",
                bool(stamp.get("unsolicited")),
            )

    def _identity(self, event: dict) -> InputIdentity | None:
        input_id = _uuid(event.get("input_id"))
        work_id = _uuid(event.get("work_id"))
        session_id = event.get("session_id")
        if input_id is None or work_id is None or not session_id:
            return None
        return InputIdentity(
            self.whose.seat.project_id,
            self.whose.conversation_id,
            self.whose.acting,
            self.whose.harness,
            str(session_id),
            input_id,
            work_id,
        )

    def _report(self, event: dict) -> Report | None:
        kind = event.get("kind")
        fact = None
        if kind == lifecycle.INPUT:
            status = event.get("status")
            if status == lifecycle.DROPPED:
                input_id = _uuid(event.get("input_id"))
                if input_id is not None and event.get("session_id"):
                    fact = InputDropped(
                        InputIdentity(
                            self.whose.seat.project_id,
                            self.whose.conversation_id,
                            self.whose.acting,
                            self.whose.harness,
                            str(event["session_id"]),
                            input_id,
                            _uuid(event.get("work_id")) or input_id,
                        ),
                        str(event.get("reason") or "dropped"),
                    )
            elif (identity := self._identity(event)) is not None:
                if status == lifecycle.WRITTEN:
                    fact = InputWritten(identity)
                elif status == lifecycle.READ:
                    fact = InputRead(
                        InputReceipt(
                            identity,
                            "native_echo",
                            execution_work_id=_uuid(event.get("execution_work_id")),
                        )
                    )
        elif kind == lifecycle.STATUS:
            work = _uuid(event.get("work_id"))
            unsolicited = bool(event.get("unsolicited"))
            if event.get("worker_status") == lifecycle.RUNNING:
                fact = SessionRunning(work, unsolicited)
            else:
                result = event.get("result") or {}
                outcome, failure = result_of(
                    self.whose, result, session_id=event.get("session_id")
                )
                if (
                    datetime.now(UTC) - datetime.fromisoformat(event["at"])
                ).total_seconds() >= STALE_S:
                    # Read hours late: the work still ends, and the room has
                    # gone on without it, so it hears nothing of how.
                    outcome = dataclasses.replace(outcome, text="", late=True)
                fact = SessionIdle(
                    work,
                    outcome,
                    tuple(
                        identifier
                        for identifier in map(_uuid, result.get("inputs") or [])
                        if identifier is not None
                    ),
                    failure,
                    unsolicited,
                )
        elif kind == lifecycle.EXITED:
            launch = str(event.get("launch") or "")
            fact = SessionExited(
                launch,
                exit_code=event.get("exit_code"),
                said=str(event.get("said") or ""),
            )
        if fact is None:
            logger.warning(
                "a runner reported an event this platform does not read: %s", event
            )
            return None
        return Report(
            self.whose.conversation_id,
            self.whose.agent_handle,
            self.whose.harness,
            self.epoch(),
            int(event["epoch"]),
            int(event["seq"]),
            datetime.fromisoformat(event["at"]),
            fact,
        )

    def frame(self) -> dict | None:
        """The last frame the session's runner reported, its age advanced by
        the time this backend has spent since hearing it."""
        heartbeat = self.state.get("heartbeat")
        if not isinstance(heartbeat, dict) or self.heard_at is None:
            return None
        age = heartbeat.get("last_frame_age_s")
        if age is not None:
            age = round(float(age) + (time.monotonic() - self.heard_at), 2)
        return {
            "work_id": heartbeat.get("work_id"),
            "last_frame_kind": heartbeat.get("last_frame_kind"),
            "last_frame_age_s": age,
        }
