"""One long-lived tmux control-mode client per agent session.

tmux is a client/server over a unix socket, and `tmux -C` is the supported way
for a program (rather than a person) to be the client: one connection, a line
protocol, and an answer for every command.

    → send-keys -t cheese -l 'hello'
    ← %begin 1786163006 366 1
    ← %end   1786163006 366 1          # or %error, with the reason above it
    ← %output %0 hello                 # the pane echoing what we injected

We drove tmux by spawning `docker exec … tmux …` per keystroke batch and
discarding the exit codes, so a failed injection was indistinguishable from a
successful one. This client makes the transport answerable.

What it deliberately does NOT claim: that anything READ the bytes. tmux reports
`send-keys` into a pane whose process has exited as success — measured, and
covered by a test — so callers must check :meth:`pane_dead` before sending and
confirm consumption out of band (the `UserPromptSubmit` hook receipt).
"""

import asyncio
import logging
import shlex
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger("cheesex.tmux_control")

# tmux answers a command with %begin/%end (or %error) carrying the same id. A
# command that outlives this is a wedged server, not a slow command — every
# command we issue is local bookkeeping.
_COMMAND_TIMEOUT_S = 10.0
# Attaching is local IPC; anything slower than this is a wedged server.
_CONNECT_TIMEOUT_S = 10.0


class TmuxControlError(RuntimeError):
    """The control connection could not be established or was lost for good."""


@dataclass
class CommandResult:
    ok: bool
    lines: list[str] = field(default_factory=list)
    error: str = ""


class TmuxControlClient:
    """Owns one `tmux -C attach` process and multiplexes commands over it.

    ``spawn`` exists so the same client works for a tmux inside a container:
    callers pass the argv prefix that reaches it (``["docker", "exec", "-i",
    name]``), and everything else is identical.
    """

    def __init__(
        self,
        socket_path: str | None,
        session: str,
        *,
        spawn_prefix: list[str] | None = None,
    ) -> None:
        # None = tmux's default socket, which is what the agent container uses.
        self._socket = socket_path
        self._session = session
        self._prefix = list(spawn_prefix or [])
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        # tmux tags each reply with the id it echoed in %begin; a command waits
        # on the future registered under that id.
        self._pending: dict[str, asyncio.Future[CommandResult]] = {}
        self._order: list[asyncio.Future[CommandResult]] = []
        self._current: tuple[str, list[str]] | None = None
        self._output_subscribers: list[Callable[[str], None]] = []
        self._lock = asyncio.Lock()
        # Resolved by the attach handshake's own result: tmux answers a
        # successful attach with %end and a failed one with %error + %exit.
        # (Attaching to a missing socket STARTS a server, so "the socket now
        # exists" and "a %-line arrived" both lie; only the result is true.)
        self._attached: asyncio.Future[bool] | None = None

    # --- lifecycle ---------------------------------------------------------

    def _argv(self) -> list[str]:
        sock = ["-S", self._socket] if self._socket else []
        return [*self._prefix, "tmux", *sock, "-C", "attach", "-t", self._session]

    async def start(self) -> None:
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self._argv(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as exc:
            raise TmuxControlError(
                f"could not start tmux control client: {exc}"
            ) from exc
        self._attached = asyncio.get_running_loop().create_future()
        self._reader_task = asyncio.create_task(self._read_loop())
        try:
            ok = await asyncio.wait_for(self._attached, timeout=_CONNECT_TIMEOUT_S)
        except TimeoutError:
            ok = False
        if ok:
            return
        await self.close()
        raise TmuxControlError(
            f"tmux control client did not attach to session {self._session!r}; "
            "no server on that socket, or no such session"
        )

    async def close(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=3)
            except (TimeoutError, ProcessLookupError):  # pragma: no cover - teardown
                self._proc.kill()

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    # --- protocol ----------------------------------------------------------

    def on_output(self, fn: Callable[[str], None]) -> None:
        """Subscribe to `%output` payloads — the pane's own byte stream, which
        is how a caller sees injected text come back without screen-scraping."""
        self._output_subscribers.append(fn)

    async def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        while True:
            raw = await self._proc.stdout.readline()
            if not raw:
                self._fail_pending("tmux control connection closed")
                return
            line = raw.decode(errors="replace").rstrip("\n")
            try:
                self._handle(line)
            except Exception:  # noqa: BLE001 — a parse slip must not kill the reader
                logger.exception("tmux control: bad line %r", line[:200])

    def _settle_attach(self, ok: bool) -> None:
        if self._attached is not None and not self._attached.done():
            self._attached.set_result(ok)

    def _handle(self, line: str) -> None:
        if line.startswith("%exit"):
            self._settle_attach(False)
            return
        if line.startswith("%output "):
            payload = line.split(" ", 2)[2] if line.count(" ") >= 2 else ""
            for fn in list(self._output_subscribers):
                try:
                    fn(payload)
                except Exception:  # noqa: BLE001 — a subscriber must not break tmux
                    logger.exception("tmux control: output subscriber raised")
            return
        if line.startswith("%begin"):
            self._current = (self._begin_id(line), [])
            return
        if line.startswith(("%end", "%error")):
            cid = self._begin_id(line)
            body = self._current[1] if self._current else []
            self._current = None
            ok = line.startswith("%end")
            self._settle_attach(ok)
            result = CommandResult(
                ok=ok,
                lines=body if ok else [],
                error="" if ok else "\n".join(body).strip(),
            )
            self._settle(cid, result)
            return
        if self._current is not None:
            self._current[1].append(line)

    @staticmethod
    def _begin_id(line: str) -> str:
        # "%begin <time> <id> <flags>" — the id is what pairs a reply with its
        # command; time alone is not unique within a second.
        parts = line.split()
        return parts[2] if len(parts) > 2 else ""

    def _settle(self, cid: str, result: CommandResult) -> None:
        fut = self._pending.pop(cid, None)
        if fut is None and self._order:
            # tmux echoes an id we did not choose, so the first command's reply
            # can arrive before we learned its id; settle in issue order.
            fut = self._order.pop(0)
        elif fut is not None and fut in self._order:
            self._order.remove(fut)
        if fut is not None and not fut.done():
            fut.set_result(result)

    def _fail_pending(self, reason: str) -> None:
        for fut in list(self._pending.values()) + list(self._order):
            if not fut.done():
                fut.set_result(CommandResult(ok=False, error=reason))
        self._pending.clear()
        self._order.clear()

    # --- commands ----------------------------------------------------------

    async def send(self, *args: str) -> CommandResult:
        """Issue one tmux command and wait for its %end/%error."""
        if not self.alive:
            return CommandResult(ok=False, error="tmux control connection is down")
        assert self._proc and self._proc.stdin
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[CommandResult] = loop.create_future()
        async with self._lock:
            self._order.append(fut)
            line = " ".join(shlex.quote(a) for a in args) + "\n"
            self._proc.stdin.write(line.encode())
            await self._proc.stdin.drain()
        try:
            return await asyncio.wait_for(fut, timeout=_COMMAND_TIMEOUT_S)
        except TimeoutError:
            if fut in self._order:
                self._order.remove(fut)
            return CommandResult(ok=False, error="tmux did not answer in time")

    async def pane_dead(self) -> bool:
        """True when the session's pane has no live process.

        Checked before every send because tmux accepts a send into a dead pane
        and reports success — the exact shape of the silent-non-delivery bug.
        """
        result = await self.send(
            "list-panes", "-t", self._session, "-F", "#{pane_dead}"
        )
        if not result.ok:
            # A pane we cannot ask about is not a pane we should paste into.
            return True
        return any(line.strip() == "1" for line in result.lines)
