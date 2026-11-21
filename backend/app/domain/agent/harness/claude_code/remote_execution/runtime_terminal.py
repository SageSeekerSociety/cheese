"""A person's shells in the environment, each one a terminal of its own.

A person working in a task opens shells here as the agent runs commands: the
same user, the same home, the project's variables (`command_env`), started in
the task's checkout. Each one is a login shell on a pseudo-terminal this
executor holds, so it lives on while nobody watches: a page that closes, a link
that drops, the device connection being released — none of them ends it. What
ends one is the person closing it (`close`), the shell exiting by itself, or
this executor stopping.

A shell belongs to the person who opened it (``owner``: the platform names
the person) and has the id the page chose when it opened it; another person's
shells are neither listed nor reached. One person keeps at most ``LIMIT`` live
shells in an environment; a shell that has ended is listed, as ended, until it
is closed, and does not count.

Watching a shell is a connection of its own: after the line that asks for it
(`serve`) the connection carries frames, a type byte and a four-byte length,
both ways. Down: ``o`` output, ``s`` a status (JSON), ``x`` the shell ended
(its exit status as text). Up: ``i`` keystrokes, ``r`` a size (JSON
``{"cols", "rows"}``). A connection that attaches is told so (``attached``),
then given what the shell last wrote (the last ``RING_BYTES`` of it, from a
line's start) before anything new, and the terminal is resized so a
full-screen program draws itself again. Several connections may watch one
shell; the size is the last one asked for.

Nothing is written to disk: what a shell wrote is kept in this process alone.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import re
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

if sys.platform != "win32":
    import fcntl
    import termios

if __package__:
    from . import runtime_core as core
else:
    import runtime_core as core

FRAME = struct.Struct(">cI")
OUTPUT, STATUS, EXIT = b"o", b"s", b"x"
INPUT, RESIZE = b"i", b"r"
#: What a shell last wrote, kept for whoever attaches next.
RING_BYTES = 256 * 1024
#: Live shells one person keeps in one environment.
LIMIT = 8
TERMINAL_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
#: A frame bigger than this from the platform is not one it sends.
MAX_FRAME = 1024 * 1024
#: How often a watcher is told whether a program is running in the shell's
#: foreground: what the platform reads as the person still using it.
BUSY_EVERY_S = 20.0
#: Frames waiting for one watcher. A watcher that stops reading holds the
#: shell's output (the shell waits) for at most ``STALL_S``, then is let go.
QUEUE_FRAMES = 64
STALL_S = 10.0


def frame(kind: bytes, payload: bytes = b"") -> bytes:
    return FRAME.pack(kind, len(payload)) + payload


def status(value: dict) -> bytes:
    return frame(STATUS, json.dumps(value).encode())


def _size(value, default):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if 0 < number <= 1000 else default


class _Watcher:
    """One connection watching a shell: its frames go out in order on a
    thread of its own, so a slow reader never stalls another."""

    def __init__(self, connection, shell):
        self.connection = connection
        self.shell = shell
        self.frames = queue.Queue(QUEUE_FRAMES)
        self.thread = threading.Thread(target=self._write, daemon=True)

    def put(self, data) -> bool:
        try:
            self.frames.put(data, timeout=STALL_S)
            return True
        except queue.Full:
            return False

    def stop(self):
        with contextlib.suppress(queue.Full):
            self.frames.put_nowait(None)
        with contextlib.suppress(OSError):
            self.connection.shutdown(socket.SHUT_RDWR)

    def _write(self):
        while True:
            try:
                data = self.frames.get(timeout=BUSY_EVERY_S)
            except queue.Empty:
                data = status({"busy": self.shell.busy()})
            if data is None:
                return
            try:
                self.connection.sendall(data)
            except OSError:
                self.stop()
                return


class Shell:
    def __init__(self, owner, terminal_id, argv, cwd, env, cols, rows):
        self.owner, self.id = owner, terminal_id
        self.lock = threading.Lock()
        self.ring = bytearray()
        self.watchers = set()
        self.ended, self.exit_code = False, None
        master, slave = os.openpty()
        self.size = (rows, cols)
        self._winsize(slave, rows, cols)

        def child():
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        try:
            self.process = subprocess.Popen(
                argv,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=cwd,
                env=env,
                preexec_fn=child,
                close_fds=True,
            )
        except BaseException:
            os.close(master)
            raise
        finally:
            os.close(slave)
        self.master = master
        threading.Thread(target=self._read, daemon=True).start()

    @staticmethod
    def _winsize(fd, rows, cols):
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def resize(self, cols, rows, nudge=False):
        """Set the terminal's size; the shell's foreground program is told
        (SIGWINCH). ``nudge`` changes it even when it is the same, so a
        full-screen program draws itself again for a watcher that just came."""
        if self.ended:
            return
        with contextlib.suppress(OSError):
            if nudge and (rows, cols) == self.size and cols > 1:
                self._winsize(self.master, rows, cols - 1)
            self._winsize(self.master, rows, cols)
            self.size = (rows, cols)

    def busy(self) -> bool:
        """Whether a program other than the shell itself holds its
        foreground: a build, a test run, an editor."""
        if self.ended:
            return False
        try:
            return os.tcgetpgrp(self.master) != self.process.pid
        except OSError:
            return False

    def write(self, data):
        if self.ended:
            return
        view = memoryview(data)
        while view:
            try:
                written = os.write(self.master, view)
            except OSError:
                return
            view = view[written:]

    def attach(self, watcher, cols, rows):
        """``watcher`` sees the shell from now on: told it attached, then
        what the shell last wrote, then everything new, in that order."""
        with self.lock:
            watcher.frames.put_nowait(
                status({"attached": self.id, "ended": self.ended, "busy": self.busy()})
            )
            if self.ring:
                watcher.frames.put_nowait(frame(OUTPUT, bytes(self.ring)))
            if self.ended:
                watcher.frames.put_nowait(frame(EXIT, str(self.exit_code).encode()))
            self.watchers.add(watcher)
        watcher.thread.start()
        self.resize(cols, rows, nudge=True)

    def detach(self, watcher):
        with self.lock:
            self.watchers.discard(watcher)
        watcher.stop()

    def _keep(self, chunk):
        self.ring.extend(chunk)
        excess = len(self.ring) - RING_BYTES
        if excess > 0:
            # From a line's start, so a replay does not begin mid-sequence.
            cut = self.ring.find(b"\n", excess)
            del self.ring[: cut + 1 if cut >= 0 else excess]

    def _send(self, data):
        with self.lock:
            watchers = list(self.watchers)
        for watcher in watchers:
            if not watcher.put(data):
                self.detach(watcher)

    def _read(self):
        while True:
            try:
                chunk = os.read(self.master, 65536)
            except OSError:
                chunk = b""
            if not chunk:
                break
            with self.lock:
                self._keep(chunk)
                watchers = list(self.watchers)
            for watcher in watchers:
                if not watcher.put(frame(OUTPUT, chunk)):
                    self.detach(watcher)
        code = self.process.wait()
        with self.lock:
            self.ended, self.exit_code = True, code
        with contextlib.suppress(OSError):
            os.close(self.master)
        self._send(frame(EXIT, str(code).encode()))

    def end(self):
        """End the shell and everything in its session."""
        if not self.ended:
            for number in (signal.SIGHUP, signal.SIGKILL):
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(self.process.pid, number)
                deadline = time.monotonic() + 2
                while self.process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.05)
                if self.process.poll() is not None:
                    break
        with self.lock:
            watchers = list(self.watchers)
            self.watchers.clear()
        for watcher in watchers:
            watcher.stop()


class Terminals:
    def __init__(self, executor):
        self.executor = executor
        self.lock = threading.Lock()
        self.shells = {}  # (owner, id) -> Shell, in the order they were opened

    def list(self, params):
        owner = str(params["owner"])
        with self.lock:
            return {
                "terminals": [
                    {"id": shell.id, "ended": shell.ended}
                    for (who, _), shell in self.shells.items()
                    if who == owner
                ]
            }

    def close(self, params):
        key = (str(params["owner"]), str(params["id"]))
        with self.lock:
            shell = self.shells.pop(key, None)
        if shell is not None:
            shell.end()
        return {"closed": shell is not None}

    def in_use(self) -> bool:
        """Whether a shell is being used: watched, or running a program in
        its foreground. An idle shell nobody watches keeps nothing up."""
        with self.lock:
            shells = list(self.shells.values())
        return any(
            not shell.ended and (shell.watchers or shell.busy()) for shell in shells
        )

    def end_all(self):
        with self.lock:
            shells = list(self.shells.values())
            self.shells.clear()
        for shell in shells:
            shell.end()

    def _cwd(self, params):
        """The task's checkout, as `task_fs` reads it: the room's workspace
        for a task with no checkout of its own."""
        if params.get("in_workspace"):
            return self.executor.root
        home = Path(self.executor.env["HOME"]).resolve()
        root = (home / ".cheese" / "tasks" / str(params["task_id"])).resolve()
        root.relative_to(home)
        return root

    def _open(self, owner, terminal_id, params, cols, rows):
        executor = self.executor
        with executor.admission_lock:
            if executor.stopping or executor.upgrading:
                return None, "stopping"
            executor.active_at = time.monotonic()
        cwd = self._cwd(params)
        if not cwd.is_dir():
            return None, "worktree"
        env = dict(executor.command_env(params.get("caller")))
        env.update(TERM="xterm-256color", COLORTERM="truecolor")
        shell_path = core.claude_shell(env) or "/bin/sh"
        with self.lock:
            found = self.shells.get((owner, terminal_id))
            if found is not None:
                return found, None
            live = sum(
                1
                for (who, _), shell in self.shells.items()
                if who == owner and not shell.ended
            )
            if live >= LIMIT:
                return None, "limit"
            shell = Shell(
                owner, terminal_id, [shell_path, "-l"], str(cwd), env, cols, rows
            )
            self.shells[(owner, terminal_id)] = shell
        executor.log("terminal", "opened", terminal=terminal_id, pid=shell.process.pid)
        return shell, None

    def serve(self, connection, reader, params):
        """Take over ``connection`` for one watcher of one shell, opening the
        shell first when this person has none by that id. ``reader`` is the
        connection's buffered reader, which may already hold the first
        frames. Returns when the watcher goes or the shell is closed."""
        if sys.platform == "win32":
            with contextlib.suppress(OSError):
                connection.sendall(status({"refused": "unsupported"}))
            return
        owner, terminal_id = str(params.get("owner") or ""), params.get("id")
        cols, rows = _size(params.get("cols"), 80), _size(params.get("rows"), 24)
        if not owner or not TERMINAL_ID.fullmatch(str(terminal_id or "")):
            shell, refused = None, "invalid"
        else:
            shell, refused = self._open(owner, terminal_id, params, cols, rows)
        if shell is None:
            with contextlib.suppress(OSError):
                connection.sendall(status({"refused": refused}))
            return
        watcher = _Watcher(connection, shell)
        shell.attach(watcher, cols, rows)
        try:
            self._take_input(shell, reader)
        finally:
            shell.detach(watcher)

    def _take_input(self, shell, reader):
        pending = bytearray()
        while True:
            try:
                chunk = reader.read1(65536)
            except (OSError, ValueError):
                return
            if not chunk:
                return
            pending.extend(chunk)
            while len(pending) >= FRAME.size:
                kind, length = FRAME.unpack_from(pending)
                if length > MAX_FRAME:
                    return
                if len(pending) < FRAME.size + length:
                    break
                payload = bytes(pending[FRAME.size : FRAME.size + length])
                del pending[: FRAME.size + length]
                if kind == INPUT:
                    self.executor.active_at = time.monotonic()
                    shell.write(payload)
                elif kind == RESIZE:
                    with contextlib.suppress(ValueError):
                        size = json.loads(payload)
                        shell.resize(
                            _size(size.get("cols"), shell.size[1]),
                            _size(size.get("rows"), shell.size[0]),
                        )
