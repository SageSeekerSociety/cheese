"""Questions asked of a session, read to the end by whichever backend process
is up when they end.

A question — a person's to their 芝士, a comment thread's, a document box's — is
a ``Consumption``: the session and the work, who takes what it says (``kind``,
``key`` and the consumer's own ``data``), until when, and the admission slots
it holds. It is written to Valkey when it begins and read by one process at a
time, the one holding its lease.

That process renews the lease every ``RENEW_S``. A process on its way out (a
deploy) stops reading and gives its leases up (``let_go``); one that dies only
stops renewing, and its leases lapse after ``LEASE_S``, before the slots they
keep do (``admission.LEASE_S``). Every process looks for unleased questions
every ``SWEEP_S`` and takes up the ones it leases first: the session is
attached again and read on from where the last reader landed, the slots are
held again (``admission.adopt``), and what was read so far comes back from the
record (``answer.Progress``). A question not yet said to its session when its
reader went away is not said again; it ends unanswered.

What the session says goes to the kind's ``Consumer``: each step to ``took``,
the end to ``ended`` once. What a viewer sees is the consumer's to publish on
the question's stream (``publish``), which a viewer reads from any process and
any point (``watch``), and which is kept for ``KEPT_S`` after the end so a
viewer that lost its connection can catch up.
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import asdict, dataclass, field, replace
from typing import Protocol

from redis.asyncio import Redis

from app.core.config import settings
from app.domain.agent.admission import Held, Slot, adopt
from app.domain.agent.session_host.answer import (
    Answer,
    Progress,
    Tool,
    Waiting,
    Words,
    follow,
    say,
    starting,
)
from app.domain.agent.session_host.contract import (
    Access,
    Prompt,
    SessionRef,
    SessionSpec,
)
from app.domain.agent.session_host.host import SessionHost

logger = logging.getLogger(__name__)

#: How long a lease lasts without being renewed, and how often it is.
LEASE_S = 20.0
RENEW_S = 5.0
#: How often a process looks for questions nobody is reading.
SWEEP_S = 5.0
#: How long a question's record and stream are kept after it ended.
KEPT_S = 3600
#: How long the record of a question that never ends is kept at most.
RECORD_S = 24 * 3600
#: How long a question taken over may go on when its reader went away before
#: writing down when it was due.
_UNKNOWN_CEILING_S = 300.0
#: The event a stream ends on.
END = "end"

_OPEN = "consumptions"


def _record(work: str) -> str:
    return f"consumption:{work}"


def _lease(work: str) -> str:
    return f"consumption:lease:{work}"


def _stream(work: str) -> str:
    return f"consumption:out:{work}"


def _current(kind: str, key: str) -> str:
    return f"consumption:of:{kind}:{key}"


# KEYS: lease. ARGV: owner, ms. Only the owner's lease is renewed or dropped.
_RENEW = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
return 0
"""
_DROP = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


@dataclass(frozen=True)
class Consumption:
    """One question being answered: what a process needs to go on reading it."""

    work_id: str
    kind: str
    key: str
    harness: str
    home: str
    #: The session host it runs on; None for the platform's own.
    host: str | None
    #: The consumer's own: who asked, where, what to answer with.
    data: dict = field(default_factory=dict)
    holds: tuple[Held, ...] = ()
    #: Whether the question reached the session, and when its answer is due
    #: (wall clock): both set when it is said.
    said: bool = False
    deadline: float | None = None
    progress: dict = field(default_factory=dict)
    ended: bool = False

    @property
    def ref(self) -> SessionRef:
        return SessionRef(self.harness, self.home)

    @property
    def work(self) -> uuid.UUID:
        return uuid.UUID(self.work_id)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: bytes | str) -> "Consumption":
        data = json.loads(raw)
        data["holds"] = tuple(Held(**held) for held in data.get("holds") or ())
        return cls(**data)


class Consumer(Protocol):
    """What one kind of question does with its answer."""

    async def stopped(self, consumption: Consumption) -> bool:
        """Did the asker stop it?"""
        ...

    async def took(
        self, consumption: Consumption, item: Waiting | Words | Tool
    ) -> None:
        """One step of the answer, as it comes."""
        ...

    async def ended(
        self,
        consumption: Consumption,
        answer: Answer,
        *,
        written: str,
        stopped: bool,
        failure: BaseException | None,
    ) -> list[tuple[str, dict]]:
        """The answer, or why there is none: keep it where it belongs, and
        return what its viewers are told last, once its slots are let go.
        ``written`` is what was handed on of it; ``failure`` what ended it
        before an answer could, if anything did."""
        ...


class Consumptions:
    """The questions this process reads, and the way to the ones nobody does."""

    def __init__(
        self,
        host: SessionHost,
        redis: Callable[[], Redis | None],
        *,
        me: str | None = None,
    ) -> None:
        self._host = host
        self._redis = redis
        self._me = me or uuid.uuid4().hex
        self._consumers: dict[str, Consumer] = {}
        self._reading: dict[str, asyncio.Task] = {}
        self._slots: dict[str, list[Slot]] = {}
        self._closing = False

    def serve(self, kind: str, consumer: Consumer) -> None:
        self._consumers[kind] = consumer

    def _valkey(self) -> Redis:
        redis = self._redis()
        if redis is None:
            raise RuntimeError("Valkey is not configured")
        return redis

    # --- beginning -----------------------------------------------------------

    async def begin(
        self,
        *,
        kind: str,
        key: str,
        data: dict,
        work_id: uuid.UUID,
        session: tuple[SessionRef, SessionSpec, Access],
        prompt: Prompt | Callable[[], Awaitable[Prompt]],
        ceiling_s: float | Callable[[], float],
        slots: list[Slot],
    ) -> Consumption:
        """Start answering: the session started, ``prompt`` said, the answer
        read, in the background. ``ceiling_s`` counts from when it is said,
        and may be asked then."""
        ref, spec, access = session
        consumption = Consumption(
            work_id=str(work_id),
            kind=kind,
            key=key,
            harness=ref.harness,
            home=ref.home,
            host=access.host,
            data=data,
            holds=tuple(slot.held() for slot in slots),
        )
        redis = self._valkey()
        await self._write(consumption)
        await redis.set(_lease(consumption.work_id), self._me, px=_ms(LEASE_S))
        await redis.sadd(_OPEN, consumption.work_id)  # type: ignore[misc]
        await redis.set(_current(kind, key), consumption.work_id, ex=RECORD_S)
        self._slots[consumption.work_id] = slots
        self._read(
            consumption, self._asked(consumption, spec, access, prompt, ceiling_s)
        )
        return consumption

    def _read(self, consumption: Consumption, reading: Awaitable[None]) -> None:
        """Read the question here, its lease renewed for as long as it is."""
        work = consumption.work_id

        async def leased() -> None:
            renewing = asyncio.ensure_future(self._renew(work))
            try:
                await reading
            finally:
                renewing.cancel()

        task = asyncio.ensure_future(leased())
        self._reading[work] = task
        task.add_done_callback(lambda _: self._reading.pop(work, None))

    async def _renew(self, work: str) -> None:
        while True:
            await asyncio.sleep(RENEW_S)
            try:
                kept = await self._valkey().eval(
                    _RENEW, 1, _lease(work), self._me, _ms(LEASE_S)
                )  # type: ignore[misc]
            except Exception:  # noqa: BLE001 — the next renewal tries again
                logger.warning("renewing the lease on %s failed", work, exc_info=True)
                continue
            if not kept:
                # Someone else reads it now: two readers say it twice.
                logger.warning("lost the lease on question %s", work)
                if task := self._reading.get(work):
                    task.cancel()
                return

    async def _asked(
        self,
        consumption: Consumption,
        spec: SessionSpec,
        access: Access,
        prompt: Prompt | Callable[[], Awaitable[Prompt]],
        ceiling_s: float | Callable[[], float],
    ) -> None:
        consumer = self._consumers[consumption.kind]
        progress = Progress()
        try:
            async for waiting in starting(
                self._host,
                consumption.ref,
                spec,
                access,
                stopped=lambda: consumer.stopped(consumption),
            ):
                await consumer.took(consumption, waiting)
            ceiling = ceiling_s() if callable(ceiling_s) else ceiling_s
            deadline = time.monotonic() + ceiling
            await say(self._host, consumption.ref, prompt, work_id=consumption.work)
            consumption = replace(
                consumption, said=True, deadline=time.time() + ceiling
            )
            await self._write(consumption)
            answer = await self._follow(consumption, progress, deadline)
            failure = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the consumer says why
            answer, failure = Answer("", str(exc)), exc
        await self._end(consumption, answer, progress, failure)

    # --- reading -------------------------------------------------------------

    async def _follow(
        self,
        consumption: Consumption,
        progress: Progress,
        deadline: float,
        *,
        recovered: bool = False,
    ) -> Answer:
        consumer = self._consumers[consumption.kind]

        async def keep(read: Progress) -> None:
            await self._write(replace(consumption, progress=read.to_json()))

        async for event in follow(
            self._host,
            consumption.ref,
            work_id=consumption.work,
            deadline=deadline,
            progress=progress,
            keep=keep,
            recovered=recovered,
        ):
            if isinstance(event, Answer):
                return event
            await consumer.took(consumption, event)
        return Answer("", "the reading ended without an answer")

    async def _end(
        self,
        consumption: Consumption,
        answer: Answer,
        progress: Progress,
        failure: BaseException | None,
    ) -> None:
        consumer = self._consumers[consumption.kind]
        redis = self._valkey()
        work = consumption.work_id
        closing: list[tuple[str, dict]] = []
        try:
            # Ended already, by a reader that went away before it was done
            # tidying up: the answer was kept and the asker told; only the
            # rest is left to do.
            if not consumption.ended:
                closing = await consumer.ended(
                    consumption,
                    answer,
                    written=progress.shown,
                    stopped=await consumer.stopped(consumption),
                    failure=failure,
                )
        except Exception:  # noqa: BLE001 — the question still ends
            logger.warning(
                "ending a %s question failed work=%s",
                consumption.kind,
                work,
                exc_info=True,
            )
        await self._write(replace(consumption, ended=True, progress=progress.to_json()))
        for slot in self._slots.pop(work, []):
            await slot.release()
        for event, data in [*closing, (END, {})]:
            await self.publish(consumption, event, data)
        await redis.expire(_record(work), KEPT_S)
        await redis.expire(_stream(work), KEPT_S)
        await redis.srem(_OPEN, work)  # type: ignore[misc]
        if (
            await redis.get(_current(consumption.kind, consumption.key))
            == work.encode()
        ):
            await redis.delete(_current(consumption.kind, consumption.key))
        await redis.eval(_DROP, 1, _lease(work), self._me)  # type: ignore[misc]

    async def _write(self, consumption: Consumption) -> None:
        await self._valkey().set(
            _record(consumption.work_id), consumption.to_json(), ex=RECORD_S
        )

    # --- what viewers see ----------------------------------------------------

    async def publish(self, consumption: Consumption, event: str, data: dict) -> None:
        """Tell the question's viewers ``event``."""
        stream = _stream(consumption.work_id)
        async with self._valkey().pipeline(transaction=False) as pipe:
            pipe.xadd(
                stream, {"event": event, "data": json.dumps(data, ensure_ascii=False)}
            )
            pipe.expire(stream, RECORD_S, nx=True)
            await pipe.execute()

    async def get(self, work_id: str) -> Consumption | None:
        raw = await self._valkey().get(_record(work_id))
        return None if raw is None else Consumption.from_json(raw)

    async def current(self, kind: str, key: str) -> Consumption | None:
        """The question of ``kind`` under ``key`` being answered now, if any."""
        work = await self._valkey().get(_current(kind, key))
        if work is None:
            return None
        consumption = await self.get(_text(work))
        return None if consumption is None or consumption.ended else consumption

    async def watch(
        self, work_id: str, after: str = "0"
    ) -> AsyncIterator[tuple[str, str, dict]]:
        """What the question's viewers were told after ``after``, and what they
        are told from now on: (position, event, data), up to its end."""
        redis = self._valkey()
        stream = _stream(work_id)
        while True:
            found: list = await redis.xread({stream: after}, count=100, block=2_000)  # type: ignore[assignment]
            if not found:
                if await redis.exists(_record(work_id)):
                    continue
                return
            for position, fields in found[0][1]:
                after = _text(position)
                event = _text(fields[b"event"])
                if event == END:
                    return
                yield after, event, json.loads(fields[b"data"])

    # --- taking over ---------------------------------------------------------

    async def run(self) -> None:
        """Take up the questions nobody reads, every ``SWEEP_S``, until
        cancelled."""
        while not self._closing:
            try:
                await self.sweep()
            except Exception:  # noqa: BLE001 — the next pass tries again
                logger.warning(
                    "looking for questions nobody reads failed", exc_info=True
                )
            await asyncio.sleep(SWEEP_S)

    async def sweep(self) -> int:
        """Take up the questions nobody holds a lease on; how many."""
        redis = self._redis()
        if redis is None:
            return 0
        taken = 0
        for raw in await redis.smembers(_OPEN):  # type: ignore[misc]
            work = _text(raw)
            if work in self._reading:
                continue
            consumption = await self.get(work)
            if consumption is None or consumption.kind not in self._consumers:
                if consumption is None:
                    await redis.srem(_OPEN, work)  # type: ignore[misc]
                continue
            if not self._reaches(consumption):
                # Its session host is connected to another process — the one
                # going away, mid-rollout — or to none: one that reaches it
                # takes it up.
                continue
            if not await redis.set(_lease(work), self._me, nx=True, px=_ms(LEASE_S)):
                continue
            logger.info("taking up question %s (%s)", work, consumption.kind)
            self._read(consumption, self._resumed(consumption))
            taken += 1
        return taken

    def _reaches(self, consumption: Consumption) -> bool:
        host = consumption.host or settings.agent_session_device_id
        return bool(host) and self._host.hub.is_online(host)

    async def _resumed(self, consumption: Consumption) -> None:
        progress = Progress.from_json(consumption.progress)
        status = None
        if not consumption.ended and progress.result is None:
            status = await self._host.attach(
                consumption.ref, Access("", host=consumption.host)
            )
            if status is None:
                # Its runner could not be asked: not gone, only not reached
                # from here. Another pass, here or elsewhere, tries again. A
                # runner the machine says is gone is an answer, not this: the
                # question ends below instead of being taken up every sweep.
                await self._valkey().eval(  # type: ignore[misc]
                    _DROP, 1, _lease(consumption.work_id), self._me
                )
                return
        slots: list[Slot] = []
        for held in consumption.holds:
            slot = await adopt(self._valkey(), held)
            if slot is None:
                # Another question of the same conversation started meanwhile:
                # this one is over.
                self._slots[consumption.work_id] = slots
                await self._end(
                    consumption,
                    Answer("", "another question took its place"),
                    progress,
                    QuestionDropped("another question took its place"),
                )
                return
            slots.append(slot)
        self._slots[consumption.work_id] = slots
        if status is None:
            await self._end(consumption, progress.result or Answer(""), progress, None)
            return
        try:
            # Said, though the reader went away before writing so down: the
            # session is working on it.
            said = consumption.said or status.work_id == consumption.work_id
            if not said:
                raise QuestionDropped("the question never reached its session")
            if not status.alive:
                raise QuestionDropped("the session ended mid-answer")
            due = consumption.deadline or time.time() + _UNKNOWN_CEILING_S
            deadline = time.monotonic() + (due - time.time())
            answer = await self._follow(consumption, progress, deadline, recovered=True)
            failure = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the consumer says why
            answer, failure = Answer("", str(exc)), exc
        await self._end(consumption, answer, progress, failure)

    async def let_go(self) -> None:
        """Stop reading, and give every lease up so the next process takes the
        questions over at once. The slots stay held for it."""
        self._closing = True
        reading = list(self._reading.items())
        for _, task in reading:
            task.cancel()
        await asyncio.gather(*(task for _, task in reading), return_exceptions=True)
        redis = self._redis()
        for work, _ in reading:
            for slot in self._slots.pop(work, []):
                slot.leave()
            if redis is not None:
                await redis.eval(_DROP, 1, _lease(work), self._me)  # type: ignore[misc]


class QuestionDropped(Exception):
    """A question taken over that cannot be answered any more."""


def _text(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


def _ms(seconds: float) -> int:
    return int(seconds * 1000)
