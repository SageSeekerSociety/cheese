"""What the session core asks of each harness (``Driver``), and what it hands one
to do it with (``Wire``).

A harness differs from the next in four things, and only in those: how its
process is started on the session host, what its runner names the verbs of
talking to it mid-work and of stopping it, how its records are read into the
platform's events (its subscription), and the shape images travel in. The rest
of driving a session — when to start it again, how a reading lands, when a
session is gone — is the core's, the same for every harness (`host.py`).
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.domain.agent.device_hub import DeviceHub
from app.domain.agent.harness import EventConsumer
from app.domain.agent.harness import SessionRef as Seat
from app.domain.agent.harness.driven.subscription import (
    Moved,
    SeatActivity,
    Subscription,
)
from app.domain.agent.harness.launch import ExecutorLaunch
from app.domain.agent.platform_failures import classify_session_start
from app.domain.agent.session_host.contract import (
    Access,
    Image,
    SessionRef,
    SessionSpec,
    StartRefused,
)
from app.domain.delivery.input_identity import (
    CompletionConsumer,
    ReceiptConsumer,
    TerminationConsumer,
)


@dataclass(frozen=True)
class Launched:
    """A session's process, as its launch left it."""

    #: The harness's own id for the conversation.
    conversation: str
    #: What its runner said it can do when it was greeted (``driven.runner``).
    capabilities: frozenset[str]
    #: What the process was started from, as the harness compares launches: a
    #: start from the same launch, while the runner answers, asks the host
    #: nothing.
    launch: str
    #: The input protocol the runner speaks, for a harness that has one.
    input_protocol: int | None = None


@dataclass(frozen=True)
class Readers:
    """Where a subscription hands what it reads, in the core's reading."""

    consume: EventConsumer
    activity: SeatActivity
    receipts: ReceiptConsumer
    completions: CompletionConsumer
    terminations: TerminationConsumer
    moved: Moved
    #: The session's controls moved (Claude Code's).
    announce: Callable[[], Awaitable[None]]


def startup_refused(log: str, *, harness: str, timed_out: bool = False) -> StartRefused:
    """The refusal for a session that did not start: one sentence, chosen from
    what the host recorded, and that record for 现场."""
    failure = classify_session_start(log, harness=harness, timed_out=timed_out)
    return StartRefused(failure.content, failure_code=failure.code, log=log)


class Wire:
    """The session host, as a driver reaches it: a program run there, a call to
    a session's runner, and the address the host reaches the platform at."""

    def __init__(self, hub: DeviceHub, api: Callable[[str], Awaitable[str]]):
        self.hub = hub
        self.api = api

    async def run(self, host: str, program: str, *, timeout: int, harness: str) -> dict:
        """A Python program run on the host (``python3 -``), to the JSON its
        last line printed; {} when it printed nothing. A program that failed
        is the session refusing to start, in the words it gave."""
        result = await self.hub.exec(
            host, ["python3", "-"], stdin=program, timeout=timeout
        )
        if result.get("exit") != 0 or result.get("truncated"):
            raise startup_refused(
                result.get("stderr") or f"{harness} did not start", harness=harness
            )
        output = (result.get("stdout") or "").strip()
        return json.loads(output.splitlines()[-1]) if output else {}

    async def call(
        self, host: str, ref: SessionRef, method: str, params: dict, **kwargs
    ) -> dict:
        return await self.hub.call_executor(host, ref.state, method, params, **kwargs)


async def start_private_executor(
    wire: Wire,
    host: str,
    executor: ExecutorLaunch,
    target: dict | None,
    env: dict[str, str],
    *,
    harness: str,
) -> None:
    """A session whose tools work in a private scratch container has it started
    before the session is: the container is the session's machine."""
    if target is not None and target.get("kind") == "private":
        await wire.run(
            host,
            executor.private_script(target, env),
            timeout=120,
            harness=harness,
        )


class Driver(Protocol):
    harness: str
    #: How the room's messages name it.
    label: str
    #: The line a reading that failed is logged under, which alerts are
    #: grouped by.
    read_failure: str
    #: The runner method that takes words said to a session mid-work.
    steer: str
    #: The runner method that takes the work away, and the key of its answer.
    stop: tuple[str, str]
    #: Whether the runner taking an input is the session reading it. False for
    #: a harness whose records say when an input was read (its subscription's
    #: ``receipt``).
    receipt_on_accept: bool
    #: The mirror's file name.
    mirror: str

    def working(self, status: dict) -> bool:
        """Whether a runner's ``ping`` says work is in flight."""
        ...

    def conversation(self, status: dict) -> str:
        """The harness's id for the conversation, from a ``ping``."""
        ...

    def takes_inputs(self, status: dict) -> bool:
        """Whether the runner can take an input whose receipt the platform
        will read."""
        ...

    async def launch(
        self,
        wire: Wire,
        host: str,
        ref: SessionRef,
        spec: SessionSpec,
        access: Access,
        known: Launched | None,
    ) -> Launched:
        """Have the session's process running from this spec. ``known`` is the
        launch this process last left it running from, while its runner has
        answered every read since."""
        ...

    def subscription(
        self,
        seat: Seat,
        acting: str,
        mirror: Path,
        call: Callable[[str, dict], Awaitable[dict]],
        launched: Launched,
        readers: Readers,
    ) -> Subscription:
        """The reading of ``seat``'s session, whose agent acts as ``acting``,
        mirrored at ``mirror``."""
        ...

    def images(self, images: tuple[Image, ...]) -> list:
        """Images as the runner takes them along with words."""
        ...

    async def adopt(self, wire: Wire, found: list[tuple[str, Access]]) -> None:
        """Make what keeps sessions on their hosts know again the ones this
        process is about to read without having started them: ``found`` is each
        one's host and access. Nothing to do for a harness whose sessions the
        host keeps by their state directory alone."""
        ...
