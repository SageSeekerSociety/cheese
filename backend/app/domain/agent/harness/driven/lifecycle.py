"""The lifecycle protocol: how a runner tells the platform what its session did.

A runner sits next to its agent process and sees every line it writes, so it is
the one place that knows, first-hand, when an input reached the agent, whether
the agent is working, how its work failed and when the process went away. It
reports exactly those facts, and the platform decides from them and from nothing
it would have to infer from a transcript. The protocol is harness-neutral: a
runner of any harness speaks it, and only how it learns each fact is its own.

**Facts, as ordered events.** Each event the runner writes goes to a durable
outbox (``Outbox``) under a monotonic sequence and the worker epoch it was
written under, naming how far the runner's journal of the session's records
had got (``records``), so the two are read in the order they happened:

- ``input`` — one input's delivery progress, by the platform's own input id:
  ``written`` (it is on the agent's input), ``read`` (the agent took it in; for
  Claude Code, its replay echo), ``dropped`` (the agent will never read it: the
  build settled it unread, or the process ended with it unread). A ``read``
  input is delivered; nothing further is tracked for it, except that a failure
  of the work it was read into names it (``status``).
- ``status`` — the declarative ``worker_status``, written on change: ``running``
  or ``idle``. A change to ``idle`` carries how the work ended (``result``): its
  closing text, its usage, the inputs read since the session last went idle,
  and, when it did not end cleanly, a typed ``failure``.
- ``exited`` — the agent process is gone, with its exit status and what it said
  if it never came up. Written once per process, by the runner that held it or,
  when that runner died with it, by the next runner on the same state.

**Delivery is pull, applied once.** The platform reads the outbox past what it
acknowledged, applies each event and its effects in one database transaction
that also records ``(session, epoch, seq)`` under a unique key, and only then
acknowledges. A redelivered event finds its key and changes nothing, so a crash
between applying and acknowledging costs a second read and nothing more — the
consumer offset stored with the effects (Kafka's exactly-once consumer, the
transactional outbox pattern).

**Epochs fence the writer.** The platform issues a worker epoch when it
registers a runner (``register``), and every later call either side makes
carries it. A runner refuses a call with an epoch other than its own, and the
platform refuses a batch whose writer epoch is not the session's current one
(``StaleEpoch``, Remote Control's 409 ``epoch_stale``): a runner replaced by a
newer registration, or a platform process another one took over from, can no
longer change anything. An event keeps the epoch it was written under, so a
backlog written before a re-registration is still delivered, once.

**Reachability is a lease, nothing more.** Every answer to a read proves the
runner reachable; one not renewed for the lease's length makes the session
*unreachable* — never exited, and nothing is settled on it (the kubelet's node
lease, Temporal's activity heartbeat). Exit is the ``exited`` event.

**What is level, not edge, is a snapshot.** The session's readiness (its model,
tools and servers) and its background tasks are reported as they stand on every
answer, so a reader that starts late or restarts has them at once.

**Stuck work is ended where it is seen.** The runner keeps the two clocks that
used to run on the platform (``Watchdog``): talking without progress, and an
input left unread. When one runs out it interrupts the agent, and the ``status``
event that follows names the failure ``stuck`` with the clock that fired.

**The ping is a handshake that outlives the protocol.** A runner lives on the
session host past the release that started it, so the next release meets
runners speaking an older version of this protocol, which it does not read. It
reads their ``ping`` instead, and these fields are the stable handshake every
version keeps: ``alive``, ``lifecycle`` (``PROTOCOL``), ``launch``,
``session_id``, ``working`` (the busy flag: a piece of work is under way, from
its first sign until its ending, while a tool runs included), ``work_id`` (the
room's work it is doing), ``worker_epoch`` (the registration it holds) and
``endings`` (its last few works' endings, oldest first, each with its ``work``,
its typed ``failure``, null when it ended cleanly, and the platform's
``inputs`` it read). A
change of ``PROTOCOL`` may add fields to the ping, never drop or redefine
these: the release that reads them drains a runner at work before replacing it
(``room.sessions.lifecycle``). ``tests/unit/test_runner_ping_handshake.py``
holds a runner of this release to them.

Standard library only: this module travels in the runner archive.
"""

import json
import sqlite3
from datetime import UTC, datetime

#: The version of this protocol a runner speaks (``ping``'s ``lifecycle``). A
#: runner of any other version is drained and replaced, never read.
PROTOCOL = 1

# --- event kinds -------------------------------------------------------------

INPUT = "input"
STATUS = "status"
EXITED = "exited"

# --- an input's delivery progress --------------------------------------------

WRITTEN = "written"
READ = "read"
DROPPED = "dropped"

# --- worker status -----------------------------------------------------------

IDLE = "idle"
RUNNING = "running"

# --- how work fails ----------------------------------------------------------

#: The model provider refused or failed the request (``status`` is its HTTP
#: status when it gave one, ``code`` the build's own name for the error).
API_ERROR = "api_error"
#: The harness failed the work for a reason of its own.
ERROR = "error"
#: Somebody took the work away.
INTERRUPTED = "interrupted"
#: The runner ended the work: one of its clocks ran out (``stuck``).
STUCK = "stuck"
#: The agent process ended in the middle of the work.
EXITED_MIDWAY = "exited"

#: The clocks a runner ends stuck work by (``Watchdog``).
NO_PROGRESS = "no_progress"
UNREAD = "unread"
#: The session stopped producing anything at all while it was at work, and
#: nothing was running on it that would explain the quiet.
SILENT = "silent"

#: How long output must be recent for a session to count as talking: talking
#: without progress for ``no_progress_s`` is a loop, while a session that went
#: quiet is the process's business, not this clock's.
TALKING_S = 300.0


class StaleEpoch(RuntimeError):
    """A call or a batch carried an epoch that is not the current one."""

    MARK = "stale worker epoch"

    def __init__(self, given: object, current: object):
        super().__init__(f"{self.MARK}: {given} is not {current}")


def stale(error: object) -> bool:
    """Whether a runner's refusal, as it came back over the wire, is a stale
    epoch (the answer carries the text only)."""
    return StaleEpoch.MARK in str(error)


# --- the outbox --------------------------------------------------------------

#: The epoch of an event about a process that was never registered: no
#: registration is ever this one, so it is never the seat's current process.
NEVER_REGISTERED = -1


class Outbox:
    """The runner's unacknowledged events, in the journal the runner keeps.

    A sequence is never reused (``AUTOINCREMENT``), so ``(epoch, seq)`` names
    one event for good. Events are deleted once acknowledged; until then every
    read returns them again. An event carries the epoch of the process it is
    about: one this runner's process wrote before any registration carries 0
    and takes the first epoch the runner is registered under; one about an
    earlier process (its ending, reported for it) carries that process's epoch,
    or ``NEVER_REGISTERED``, and is never restamped.
    """

    SCHEMA = """
        CREATE TABLE IF NOT EXISTS lifecycle (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            epoch INTEGER NOT NULL,
            at TEXT NOT NULL,
            event TEXT NOT NULL
        );
    """
    #: The most events one read hands over.
    PAGE = 256

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.connection.executescript(self.SCHEMA)
        self.epoch = 0

    def register(self, epoch: int) -> None:
        if epoch < self.epoch:
            raise StaleEpoch(epoch, self.epoch)
        self.epoch = epoch
        with self.connection:
            self.connection.execute(
                "UPDATE lifecycle SET epoch=? WHERE epoch=0", (epoch,)
            )

    def check(self, epoch: object) -> None:
        """Refuse a call that does not carry this runner's epoch."""
        if epoch != self.epoch or not self.epoch:
            raise StaleEpoch(epoch, self.epoch)

    def emit(self, kind: str, *, records: int, epoch: int | None = None, **body) -> int:
        """Write an event; ``records`` is how far the runner's journal had got
        when it happened, so a reader shows what came before it first.
        ``epoch``, for an event about an earlier process, is the epoch that
        process was registered under; this runner's own otherwise."""
        body["records"] = records
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO lifecycle(epoch, at, event) VALUES (?, ?, ?)",
                (
                    self.epoch if epoch is None else epoch,
                    datetime.now(UTC).isoformat(),
                    json.dumps({"kind": kind, **body}, ensure_ascii=False),
                ),
            )
        return int(cursor.lastrowid or 0)

    def pending(self) -> list[dict]:
        return [
            {"epoch": epoch, "seq": seq, "at": at, **json.loads(event)}
            for seq, epoch, at, event in self.connection.execute(
                "SELECT seq, epoch, at, event FROM lifecycle "
                "WHERE epoch != 0 ORDER BY seq LIMIT ?",
                (self.PAGE,),
            )
        ]

    def ack(self, through: int) -> None:
        if through > 0:
            with self.connection:
                self.connection.execute(
                    "DELETE FROM lifecycle WHERE seq <= ?", (through,)
                )

    def last(self) -> int:
        (last,) = self.connection.execute("SELECT MAX(seq) FROM lifecycle").fetchone()
        return last or 0

    def empty(self) -> bool:
        return (
            self.connection.execute("SELECT 1 FROM lifecycle LIMIT 1").fetchone()
            is None
        )


# --- stuck work --------------------------------------------------------------


class Watchdog:
    """The clocks a runner ends stuck work by, on the monotonic clock.

    - **no progress** — the session said something within ``TALKING_S`` but
      has called no tool and finished nothing for ``no_progress_s``.
    - **unread** — an input written to the agent has not been read for
      ``unread_s``. Input is read at tool boundaries, so the clock does not run
      while a tool runs, and after one it runs from the return; before the work
      did anything at all there is no evidence of reading either way.
    - **silent** — the session has produced nothing at all for ``silent_s``
      and no tool is running. The other two both need something to go on: a
      session that said something recently, or an input still waiting to be
      read. A session whose process is alive but whose build stopped answering
      has neither, and without this clock nothing ever reports that it
      stopped — the work stays open, and so does everything that counts it.

    0 turns a clock off. The clocks run only while the session is running.
    """

    def __init__(
        self,
        *,
        no_progress_s: float = 0.0,
        unread_s: float = 0.0,
        silent_s: float = 0.0,
    ):
        self.no_progress_s = no_progress_s
        self.unread_s = unread_s
        self.silent_s = silent_s
        self.running_since: float | None = None
        self.progressed = 0.0
        self.said: float | None = None
        self.returned: float | None = None
        self.tools: set[str] = set()
        self.seen = False
        #: input id → when it was written, while it is unread.
        self.unread: dict[str, float] = {}

    def configure(
        self, *, no_progress_s: float, unread_s: float, silent_s: float = 0.0
    ) -> None:
        self.no_progress_s, self.unread_s = no_progress_s, unread_s
        self.silent_s = silent_s

    def armed(self) -> bool:
        """Whether any clock is on."""
        return self.no_progress_s > 0 or self.unread_s > 0 or self.silent_s > 0

    def started(self, now: float) -> None:
        self.running_since = self.progressed = now
        self.said = self.returned = None
        self.tools = set()
        self.seen = False

    def stopped(self) -> None:
        self.running_since = None

    def written(self, identifier: str, now: float) -> None:
        self.unread.setdefault(identifier, now)

    def taken(self, identifier: str) -> None:
        """The input was read, or will never be."""
        self.unread.pop(identifier, None)

    def output(self, now: float) -> None:
        self.seen = True
        self.said = now

    def progress(self, now: float) -> None:
        self.seen = True
        self.progressed = now

    def tool_started(self, call: str, now: float) -> None:
        self.progress(now)
        self.tools.add(call)

    def tool_returned(self, call: str, now: float) -> None:
        self.progress(now)
        self.tools.discard(call)
        self.returned = now

    def fired(self, now: float) -> str | None:
        """Which clock ran out, if one did."""
        if self.running_since is None:
            return None
        if (
            self.no_progress_s > 0
            and self.said is not None
            and now - self.said < TALKING_S
            and now - self.progressed >= self.no_progress_s
        ):
            return NO_PROGRESS
        if self.unread_s > 0 and self.seen and not self.tools and self.unread:
            waiting = max(
                min(self.unread.values()), self.returned or 0.0, self.running_since
            )
            if now - waiting >= self.unread_s:
                return UNREAD
        if self.silent_s > 0 and not self.tools:
            # Nothing ran and nothing was said: what a build that stopped
            # answering looks like from here. A tool in flight can be quiet
            # for as long as it likes — that is the build working, not dying.
            quiet_since = max(self.progressed, self.said or 0.0)
            if now - quiet_since >= self.silent_s:
                return SILENT
        return None
