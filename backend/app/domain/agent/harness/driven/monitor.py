"""Monitor: a command on the room's machine whose every line of output is news.

Claude Code has a `Monitor` tool: it starts a command in the background and
tells the session each line the command prints, as it prints it, while the
session goes on working; between turns a line wakes it. Codex and pi have
nothing like it, so a session of theirs watching a deploy, a log or a pull
request polled with `sleep` in a loop — each poll a tool call the model waited
on. This is that tool for them, with Claude Code's semantics as the 2.1.282
build states and implements them, so the room's instructions for watching
something read the same on every harness:

- The command runs on the room's machine, in the shell every harness runs the
  model's commands in, from the checkout; the call returns at once with an id.
- Each non-empty stdout line is an event. Lines printed within `BATCH_S` of the
  first are one notification. A line longer than `LINE_MAX` and a notification
  longer than `EVENT_MAX` are cut. Stderr is not an event.
- The watch ends when the command exits (the status is told), when it is
  stopped (`TaskStop` with its id), or at its deadline: `timeout_ms`, default
  `DEFAULT_TIMEOUT_MS`, at most `MAX_TIMEOUT_MS`, when it is killed and the
  session hears how many events it delivered.
- A command that prints faster than the session can read is held to a bucket of
  `BURST` notifications refilled one per `REFILL_S`; what does not fit is
  counted and said, and a flood that lasts `FLOOD_S` stops the monitor.
- The project's `permissions.deny` rules for Bash are the rules for its command,
  as `proxy.js` holds a Claude Code room's Monitor to them.

What a harness supplies is how to reach the machine (`control`, the executor's
`control` call) and how to say something to the session (`tell`: into the turn
it is in, or as a turn of its own when it is idle).

Standard library only: this runs in the runner archive on the session host.
"""

import asyncio
import base64
import codecs
import contextlib
import signal
import sys
import time
import uuid
from collections.abc import Awaitable, Callable

#: The deadline a monitor gets when none is asked for, and the longest one
#: given (Claude Code 2.1.282 in a remote session: `zot`, `o`).
DEFAULT_TIMEOUT_MS = 300_000
MAX_TIMEOUT_MS = 1_800_000
#: Lines this close together are one notification (its `J`).
BATCH_S = 0.2
#: Where one line, and one notification, is cut (its `A` and `L`).
LINE_MAX = 500
EVENT_MAX = 3000
#: How much of a line with no newline yet is kept (its `D`).
PENDING_MAX = 1024 * 1024
#: The rate a session is told at: a bucket of this many notifications, one
#: more every `REFILL_S` (its `Tde` and `sre`); and how long a flood may be
#: suppressed before the monitor is stopped (its `Z`).
BURST = 10
REFILL_S = 2.0
FLOOD_S = 30.0

#: How long one read of the command waits on the machine for more output, and
#: how long a read the machine did not answer waits before asking again.
READ_WAIT_S = 2.0
RETRY_S = 5.0
#: How long a stopped command has to end on SIGTERM before it gets SIGKILL.
STOP_GRACE_S = 5.0

PREFIX = "monitor-"

DESCRIPTION = "\n".join(
    (
        (
            "Start a background monitor that streams events from a long-running "
            "script. Each stdout line is an event — you keep working and "
            "notifications arrive in the chat. Events arrive on their own schedule "
            "and are not replies from the user, even if one lands while you're "
            "waiting for the user to answer a question."
        ),
        "Pick by how many notifications you need:",
        (
            "- **One per occurrence, until the monitor expires (re-arm to "
            'continue)** ("tell me every time an ERROR line appears") → Monitor '
            "with an unbounded command (`tail -f`, `inotifywait -m`, `while true`)."
        ),
        (
            '- **One per occurrence, until a known end** ("emit each CI step '
            'result, stop when the run completes") → Monitor with a command that '
            "emits lines and then exits."
        ),
        (
            '- **One** ("tell me when the server is ready / the build finishes") → '
            "run the command in the background and wait for it to end, with a "
            "command that exits when the condition is true, e.g. `until grep -q "
            '"Ready in" dev.log; do sleep 0.5; done`.'
        ),
        (
            "Your script's stdout is the event stream. Each line becomes a "
            "notification. Exit ends the watch."
        ),
        "  # Each matching log line is an event",
        '  tail -f /var/log/app.log | grep --line-buffered "ERROR"',
        (
            "  # Per-occurrence with a natural end: emit each CI check as it lands,"
            " exit when the run completes"
        ),
        '  prev=""',
        "  while true; do",
        "    s=$(gh pr checks 123 --json name,bucket)",
        (
            '    cur=$(jq -r \'.[] | select(.bucket!="pending") | "\\(.name): '
            '\\(.bucket)"\' <<<"$s" | sort)'
        ),
        '    comm -13 <(echo "$prev") <(echo "$cur")',
        "    prev=$cur",
        '    jq -e \'all(.bucket!="pending")\' <<<"$s" >/dev/null && break',
        "    sleep 30",
        "  done",
        (
            "**Don't use an unbounded command for a single notification.** `tail "
            "-f`, `inotifywait -m`, and `while true` never exit on their own, so "
            "the monitor stays armed until timeout even after the event has fired."
        ),
        "**Script quality:**",
        (
            "- Every pipe stage must flush per line or matches sit in its buffer "
            "unseen: `grep` needs `--line-buffered`, `awk` needs `fflush()`. `head`"
            " cannot flush at all."
        ),
        (
            "- In poll loops, handle transient failures (`curl ... || true`) — one "
            "failed request shouldn't kill the monitor."
        ),
        (
            "- Poll intervals: 30s+ for remote APIs (rate limits), 0.5-1s for local"
            " checks."
        ),
        (
            "- Write a specific `description` — it appears in every notification "
            '("errors in deploy.log" not "watching logs").'
        ),
        (
            "- Only stdout is the event stream; stderr is not an event. For a "
            "command you run directly, merge stderr with `2>&1` so its failures "
            "reach your filter."
        ),
        (
            "**Coverage — silence is not success.** When watching a job or process "
            "for an outcome, your filter must match every terminal state, not just "
            "the happy path: emit on every terminal status "
            "(`succeeded|failed|cancelled|timeout`), not just success."
        ),
        (
            "**Output volume**: Every stdout line is a conversation message, so the"
            " filter should be selective — the lines you'd act on, not only good "
            "news. Never pipe raw logs. Monitors that produce too many events are "
            "automatically stopped; restart with a tighter filter if this happens."
        ),
        (
            "Stdout lines within 200ms are batched into a single notification, so "
            "multiline output from a single event groups naturally."
        ),
        (
            "The script runs on the room's machine, in the same shell environment "
            "and directory as Bash. Exit ends the watch (exit code is reported). "
            "Every monitor expires after `timeout_ms` (default 5 minutes, at most "
            "30 minutes): it is killed and you get one notice with the event count."
            " Re-arm it if you still need the watch; for a long watch (PR "
            "monitoring, log tails) set `timeout_ms` to the maximum and re-arm on "
            "each expiry. Use TaskStop with the monitor's task id to cancel early."
        ),
    )
)

TOOL = {
    "name": "Monitor",
    "description": DESCRIPTION,
    "inputSchema": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command or script. Each stdout line is an "
                "event; exit ends the watch.",
            },
            "description": {
                "type": "string",
                "description": "Short human-readable description of what you are "
                "monitoring (shown in notifications).",
            },
            "timeout_ms": {
                "type": "integer",
                "description": f"Kill the monitor after this deadline. Default "
                f"{DEFAULT_TIMEOUT_MS}ms. Deadlines above {MAX_TIMEOUT_MS}ms are "
                f"capped to {MAX_TIMEOUT_MS}ms. You are notified at expiry and "
                "can re-arm.",
            },
        },
        "required": ["command", "description"],
    },
}


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def notification(task: str, summary: str, *, event: str = "", status: str = "") -> str:
    """What the session is told, in the shape Claude Code tells it a task's
    news: the task's id, a summary, and the event itself."""
    parts = ["<task-notification>", f"<task-id>{task}</task-id>"]
    if status:
        parts.append(f"<status>{status}</status>")
    parts.append(f"<summary>{_escape(summary)}</summary>")
    if event:
        parts.append(f"<event>{_escape(event)}</event>")
    parts.append("</task-notification>")
    return "\n".join(parts)


def _duration(ms: int) -> str:
    minutes, seconds = divmod(round(ms / 1000), 60)
    if minutes and seconds:
        return f"{minutes}m {seconds}s"
    return f"{minutes}m" if minutes else f"{seconds}s"


def _cut(line: str, limit: int, mark: str) -> str:
    return line[:limit] + mark if len(line) > limit else line


class Lines:
    """A command's stdout as the lines it is made of: whole lines only, each
    stripped, the empty ones dropped, a long one cut."""

    def __init__(self) -> None:
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.pending = ""

    def feed(self, data: bytes) -> list[str]:
        self.pending += self.decoder.decode(data)
        *whole, self.pending = self.pending.split("\n")
        if len(self.pending) > PENDING_MAX:
            self.pending = self.pending[:PENDING_MAX]
        return [
            _cut(line.strip(), LINE_MAX, "...(truncated)")
            for line in whole
            if line.strip()
        ]

    def rest(self) -> list[str]:
        """The last line, which the command ended without a newline after."""
        line, self.pending = (self.pending + self.decoder.decode(b"", True)).strip(), ""
        return [_cut(line, LINE_MAX, "...(truncated)")] if line else []


class Bucket:
    """At most `BURST` at once, one more every `REFILL_S`."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self.clock = clock
        self.tokens = BURST
        self.at = clock()

    def take(self) -> bool:
        gained = int((self.clock() - self.at) // REFILL_S)
        if gained > 0:
            self.tokens = min(BURST, self.tokens + gained)
            self.at += gained * REFILL_S
        if self.tokens > 0:
            self.tokens -= 1
            return True
        return False


class Monitor:
    def __init__(
        self,
        owner: "Monitors",
        task: str,
        description: str,
        timeout_ms: int,
    ) -> None:
        self.owner = owner
        self.task = task
        self.description = description
        self.timeout_ms = timeout_ms
        self.lines = Lines()
        self.batch: list[str] = []
        self.flusher: asyncio.TimerHandle | None = None
        self.bucket = Bucket(owner.clock)
        self.events = 0
        self.printed = False
        self.suppressed = 0
        self.flood_since: float | None = None
        self.last_suppressed: float | None = None
        self.closed = False
        self.stopping = False
        self.follower: asyncio.Task | None = None

    # --- what the session hears ---------------------------------------------

    async def _say(self, text: str) -> None:
        try:
            await self.owner.tell(text)
        except Exception as error:  # the watch goes on
            print(f"[cheese] monitor {self.task} untold: {error!r}", file=sys.stderr)

    def _event(self, body: str) -> str:
        return notification(
            self.task,
            f'Monitor event: "{self.description}"',
            event=_cut(body, EVENT_MAX, " ...(truncated)"),
        )

    def _admit(self) -> bool:
        """Whether a notification may go now; what was held back meanwhile is
        said first (Claude Code's `avn`)."""
        now = self.owner.clock()
        if not self.bucket.take():
            self.suppressed += 1
            self.last_suppressed = now
            if self.flood_since is None:
                self.flood_since = now
            return False
        if self.suppressed:
            self.owner.spawn(
                self._say(
                    self._event(
                        f"[{self.suppressed} events suppressed — output rate too "
                        "high. Consider using TaskStop to restart this monitor with "
                        "a more selective filter.]"
                    )
                )
            )
            self.suppressed = 0
            if (
                self.last_suppressed is not None
                and now - self.last_suppressed > REFILL_S * 3
            ):
                self.flood_since = None
        self.events += 1
        return True

    def _flush(self) -> None:
        self.flusher = None
        if self.closed or not self.batch:
            return
        body, self.batch = "\n".join(self.batch), []
        if self._admit():
            self.owner.spawn(self._say(self._event(body)))
            return
        now = self.owner.clock()
        if self.flood_since is not None and now - self.flood_since > FLOOD_S:
            self.closed = True
            seconds = round(now - self.flood_since)
            self.owner.spawn(self._stop_command())
            self.owner.spawn(
                self._say(
                    self._event(
                        f"[Monitor stopped — too much output ({self.suppressed} "
                        f"events suppressed over {seconds}s). Restart with a more "
                        "selective source.]"
                    )
                )
            )

    def _feed(self, data: bytes) -> None:
        if data:
            self.printed = True
        lines = self.lines.feed(data)
        if not lines or self.closed:
            return
        self.batch.extend(lines)
        if self.flusher is None:
            self.flusher = asyncio.get_running_loop().call_later(BATCH_S, self._flush)

    # --- following the command ----------------------------------------------

    async def _stop_command(self) -> None:
        """SIGTERM now, SIGKILL after `STOP_GRACE_S` to whatever ignored it;
        returns without waiting for the second."""
        with contextlib.suppress(Exception):
            await self.owner.signal(self.task, signal.SIGTERM)
        self.owner.spawn(self._kill_later())

    async def _kill_later(self) -> None:
        await asyncio.sleep(STOP_GRACE_S)
        # Expiry retires the watch before this grace period ends. The executor,
        # which owns the command's lifetime, knows whether it still needs killing.
        with contextlib.suppress(Exception):
            await self.owner.signal(self.task, signal.SIGKILL)

    async def follow(self) -> None:
        out = err = 0
        deadline = self.owner.clock() + self.timeout_ms / 1000
        try:
            while True:
                left = deadline - self.owner.clock()
                if left <= 0:
                    await self._expire()
                    return
                try:
                    answer = await asyncio.to_thread(
                        self.owner.control,
                        {
                            "subtype": "shell",
                            "operation": "read",
                            "command_id": self.task,
                            "out": out,
                            "err": err,
                            "wait": min(READ_WAIT_S, left),
                        },
                    )
                except Exception as error:  # the machine may come back
                    print(
                        f"[cheese] monitor {self.task} unread: {error!r}",
                        file=sys.stderr,
                    )
                    await asyncio.sleep(min(RETRY_S, max(left, 0)))
                    continue
                data = base64.b64decode(answer.get("out") or "")
                out += len(data)
                # Not an event, but read all the same: the machine says the
                # command ended only once everything it printed has been read.
                err += len(base64.b64decode(answer.get("err") or ""))
                self._feed(data)
                if "exit" in answer:
                    await self._ended(int(answer["exit"]))
                    return
                if answer.get("lost"):
                    await self._ended(None)
                    return
        finally:
            self.owner.forget(self.task)

    async def _ended(self, status: int | None) -> None:
        if self.closed:
            return
        self.closed = True
        if self.flusher is not None:
            self.flusher.cancel()
            self.flusher = None
        last = "\n".join([*self.batch, *self.lines.rest()])
        self.batch = []
        if self.stopping:
            return
        if last and self._admit():
            await self._say(self._event(last))
        if status is None:
            summary, state = (
                f'Monitor "{self.description}" lost: the machine\'s execution '
                "service restarted before the script ended",
                "failed",
            )
        elif status != 0:
            summary, state = (
                f'Monitor "{self.description}" script failed (exit {status})',
                "failed",
            )
        elif not self.printed:
            summary, state = (
                f'Monitor "{self.description}" ended without producing output '
                f"(exit {status})",
                "completed",
            )
        else:
            summary, state = (
                f'Monitor "{self.description}" stream ended (exit {status})',
                "completed",
            )
        await self._say(notification(self.task, summary, status=state))

    async def _expire(self) -> None:
        self.closed = True
        if self.flusher is not None:
            self.flusher.cancel()
            self.flusher = None
        self.batch = []
        await self._stop_command()
        after = _duration(self.timeout_ms)
        summary = (
            f"[Monitor expired after {after} with no events delivered. Re-arm it if "
            "you still need the watch — and widen the filter if silence was "
            "unexpected.]"
            if self.events == 0
            else f"[Monitor expired after {after} with {self.events} "
            f"event{'' if self.events == 1 else 's'} delivered. Re-arm it if you "
            "still need the watch.]"
        )
        await self._say(
            notification(
                self.task, f'Monitor "{self.description}" expired', event=summary
            )
        )


class Monitors:
    """Every monitor one session has running.

    ``control`` is the room machine's executor `control` call (blocking; run
    in a thread), ``tell`` says something to the session, ``cwd`` is where its
    commands start, ``place`` writes a command's paths where the machine holds
    what they name (a harness that shows the session copies on this host), and
    ``take`` (optional, blocking) holds the machine before a command is started
    on it, for a harness that does more than the executor's own taking (pi's
    `Machine.take`)."""

    def __init__(
        self,
        control: Callable[[dict], dict],
        tell: Callable[[str], Awaitable[None]],
        *,
        cwd: Callable[[], str],
        place: Callable[[str], str] = str,
        take: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.control = control
        self.tell = tell
        self.cwd = cwd
        self.place = place
        self.take = take
        self.clock = clock
        self.running: dict[str, Monitor] = {}
        self.chores: set[asyncio.Task] = set()

    def spawn(self, work: Awaitable[None]) -> None:
        task = asyncio.ensure_future(work)
        self.chores.add(task)
        task.add_done_callback(self.chores.discard)

    def forget(self, task: str) -> None:
        self.running.pop(task, None)

    def count(self) -> int:
        return len(self.running)

    async def signal(self, task: str, number: int) -> bool:
        answer = await asyncio.to_thread(
            self.control,
            {
                "subtype": "shell",
                "operation": "signal",
                "command_id": task,
                "signal": int(number),
            },
        )
        return bool(answer.get("running"))

    async def start(self, arguments: dict, call_id: str) -> str:
        """Start one; what the call that started it returns. Raises
        `ValueError` with the reason when it is not started."""
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            # i18n-exempt: runner bundle: session host, stdlib only, no catalog
            raise ValueError("Monitor needs a command: each stdout line is an event")
        if arguments.get("ws"):
            # i18n-exempt: runner bundle: session host, stdlib only, no catalog
            raise ValueError(
                "Monitor's ws source is unavailable here; give it a command "
                "instead (it runs on the work machine)."
            )
        description = str(arguments.get("description") or "").strip() or command[:80]
        asked = arguments.get("timeout_ms")
        timeout_ms = (
            min(int(asked), MAX_TIMEOUT_MS)
            if isinstance(asked, (int, float))
            and not isinstance(asked, bool)
            and asked > 0
            else DEFAULT_TIMEOUT_MS
        )
        if self.take is not None:
            await asyncio.to_thread(self.take)
        command = self.place(command)
        # The project's rules for Bash hold for a command run this way too.
        verdict = await asyncio.to_thread(
            self.control,
            {
                "subtype": "tool_hooks",
                "event": "PreToolUse",
                "tool": "Bash",
                "args": {"command": command},
                "request_id": call_id,
                "fire": False,
            },
        )
        if "denied" in verdict:
            raise ValueError(verdict["denied"])
        task = PREFIX + uuid.uuid4().hex[:12]
        answer = await asyncio.to_thread(
            self.control,
            {
                "subtype": "shell",
                "operation": "start",
                "command_id": task,
                # The shell every harness runs the model's commands in.
                "kind": "bash",
                "body": command,
                "cwd": self.cwd(),
                # Only stdout is the event stream.
                "merge": False,
            },
        )
        if not answer.get("started"):
            # i18n-exempt: runner bundle: session host, stdlib only, no catalog
            raise ValueError("The monitor's command was stopped before it started")
        monitor = Monitor(self, task, description, timeout_ms)
        self.running[task] = monitor
        monitor.follower = asyncio.ensure_future(monitor.follow())
        return (
            f"Monitor started (task {task}, expires in {_duration(timeout_ms)} unless "
            "the source ends first; you get one notice at expiry — re-arm if you "
            "still need the watch). You will be notified on each event. Keep "
            "working — do not poll or sleep. Events may arrive while you are "
            "waiting for the user — an event is not their reply."
        )

    async def stop(self, task: str) -> str:
        """`TaskStop` for a monitor: what it answers."""
        monitor = self.running.get(task)
        if monitor is None:
            return f"No running monitor with id {task}"
        monitor.stopping = True
        await monitor._stop_command()
        return f"Monitor {task} ({monitor.description}) stopped"

    async def close(self) -> None:
        """The session is going: so are its monitors, and nothing more is said."""
        for monitor in list(self.running.values()):
            monitor.stopping = True
            with contextlib.suppress(Exception):
                await self.signal(monitor.task, signal.SIGKILL)
            if monitor.follower is not None:
                monitor.follower.cancel()
        followers = [m.follower for m in self.running.values() if m.follower]
        # Every command is killed above, and nothing more is to be said.
        for chore in self.chores:
            chore.cancel()
        await asyncio.gather(*followers, *self.chores, return_exceptions=True)
        self.running.clear()
