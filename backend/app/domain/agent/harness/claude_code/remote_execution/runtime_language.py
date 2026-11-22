"""Language servers beside a task's checkout, one per connection.

A person reading a task's files in the page asks a language server about
them: where a name is defined, what is wrong with a line. The server runs
here, in the task's checkout (`core.task_checkout`), started plainly as an
editor would start it, so it uses whatever configuration the repository has —
the platform does not guess how a project builds. It runs with the
environment and identity a person's shell has (`command_env` with the
person's attachment as the caller), on pipes rather than a pseudo-terminal.

Which program serves which language is data (``SERVERS``): one entry names
the languages it serves and the command line that starts it, so another
language is one more entry.

A connection asks for one (`serve`) with a line naming the language and
whose it is, and is answered with one JSON line: ``{"result": {"root": …}}``
— the checkout the server runs in — or ``{"error": reason}``: ``unavailable``
(no server for that language, or its program is not on this machine),
``worktree`` (the task has no checkout here), ``limit`` (the person already
runs ``LIMIT`` servers for this task), ``stopping`` (this executor is
stopping or upgrading), ``unsupported``, ``invalid``. After a result the
connection carries the server's stdin and stdout verbatim, both ways; what it
writes to stderr is not mixed in, and its last ``STDERR_TAIL`` bytes are
logged when it ends. The connection closing ends the server and everything
it started; the server ending closes the connection.

A language server is nobody's work on the machine: it does not move the idle
clock, it does not hold off an idle upgrade or the idle stop, and the stop
ends it with the rest (`end_all`).
"""

from __future__ import annotations

import collections
import contextlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

if __package__:
    from . import runtime_core as core
else:
    import runtime_core as core

#: Who serves which language, and how it is started. ``argv[0]`` is looked up
#: on the ``PATH`` a person's shell has.
SERVERS = (
    {
        "languages": ("c", "cpp"),
        # An editor session over stdio in a memory-capped sandbox: no
        # background index of the whole project (it would hold the project's
        # symbols in memory and persist them under the checkout's
        # `.cache/clangd/index`, where they show up as changes), compiled
        # preambles kept on disk rather than in memory, two workers.
        "argv": (
            "clangd",
            "--background-index=false",
            "--pch-storage=disk",
            "-j=2",
            "--log=error",
        ),
    },
)
#: Language servers one person runs at once for one task.
LIMIT = 2
#: What is kept of a server's stderr, for the log when it ends.
STDERR_TAIL = 4096
#: How long an ended connection's server is given to exit before it is killed.
GRACE_S = 2.0


def server_for(language):
    """The registry entry serving ``language``, or None."""
    for entry in SERVERS:
        if language in entry["languages"]:
            return entry
    return None


def _answer(connection, value):
    with contextlib.suppress(OSError):
        connection.sendall(json.dumps(value).encode() + b"\n")


class _Server:
    def __init__(self, owner, task, language, process):
        self.owner, self.task, self.language = owner, task, language
        self.process = process
        self.stderr = collections.deque()
        self.stderr_size = 0

    def keep_stderr(self):
        while True:
            try:
                chunk = self.process.stderr.read(65536)
            except (OSError, ValueError):
                return
            if not chunk:
                return
            self.stderr.append(chunk)
            self.stderr_size += len(chunk)
            while self.stderr_size - len(self.stderr[0]) >= STDERR_TAIL:
                self.stderr_size -= len(self.stderr.popleft())

    def write(self, data) -> bool:
        """Hand ``data`` to the server's stdin, all of it; False once it no
        longer reads."""
        view = memoryview(data)
        while view:
            try:
                written = self.process.stdin.write(view)
            except (OSError, ValueError):
                return False
            view = view[written or 0 :]
        return True

    def end(self):
        """End the server and everything it started, and reap it."""
        with contextlib.suppress(OSError, ValueError):
            self.process.stdin.close()
        for number in (signal.SIGTERM, signal.SIGKILL):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(self.process.pid, number)
            deadline = time.monotonic() + GRACE_S
            while self.process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
        # What the leader left in its group goes with it.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(self.process.pid, signal.SIGKILL)
        return self.process.wait()


class Languages:
    def __init__(self, executor):
        self.executor = executor
        self.lock = threading.Lock()
        self.servers = set()

    def end_all(self):
        with self.lock:
            servers = list(self.servers)
        for server in servers:
            server.end()

    def _start(self, params):
        """The server this connection asks for, started; or why not."""
        executor = self.executor
        owner, language = str(params.get("owner") or ""), params.get("language")
        if not owner or not isinstance(language, str):
            return None, None, "invalid"
        entry = server_for(language)
        if entry is None:
            return None, None, "unavailable"
        with executor.admission_lock:
            # Nothing new starts on an executor on its way out; and asking is
            # not using it, so the idle clock stays where it is.
            if executor.stopping or executor.upgrading:
                return None, None, "stopping"
        try:
            cwd = core.task_checkout(executor, params)
        except (KeyError, ValueError):
            return None, None, "invalid"
        if not cwd.is_dir():
            return None, None, "worktree"
        env = dict(executor.command_env(params.get("caller")))
        program = shutil.which(entry["argv"][0], path=env.get("PATH"))
        if program is None:
            return None, None, "unavailable"
        task = str(params.get("task_id"))
        with self.lock:
            running = sum(
                1
                for server in self.servers
                if (server.owner, server.task) == (owner, task)
                and server.process.poll() is None
            )
            if running >= LIMIT:
                return None, None, "limit"
            process = subprocess.Popen(
                [program, *entry["argv"][1:]],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(cwd),
                env=env,
                start_new_session=True,
                close_fds=True,
                bufsize=0,
            )
            server = _Server(owner, task, language, process)
            self.servers.add(server)
        executor.log("language", "started", language=language, pid=process.pid)
        return server, cwd, None

    def serve(self, connection, reader, params):
        """Take over ``connection`` for one language server: answer, then
        relay its stdio until either side ends. ``reader`` is the
        connection's buffered reader, which may already hold the first
        bytes."""
        if sys.platform == "win32":
            _answer(connection, {"error": "unsupported"})
            return
        try:
            server, cwd, refused = self._start(params)
        except OSError:
            server, cwd, refused = None, None, "unavailable"
        if server is None:
            _answer(connection, {"error": refused})
            return
        _answer(connection, {"result": {"root": str(cwd)}})
        stderr = threading.Thread(target=server.keep_stderr, daemon=True)
        stderr.start()
        output = threading.Thread(
            target=self._pass_output, args=(server, connection), daemon=True
        )
        output.start()
        try:
            while True:
                try:
                    chunk = reader.read1(65536)
                except (OSError, ValueError):
                    break
                if not chunk:
                    break
                if not server.write(chunk):
                    break
        finally:
            code = server.end()
            output.join(timeout=GRACE_S)
            stderr.join(timeout=GRACE_S)
            with self.lock:
                self.servers.discard(server)
            with contextlib.suppress(OSError):
                connection.shutdown(socket.SHUT_RDWR)
            self.executor.log(
                "language",
                "ended",
                language=server.language,
                code=code,
                stderr=b"".join(server.stderr)[-STDERR_TAIL:].decode(errors="replace"),
            )

    @staticmethod
    def _pass_output(server, connection):
        """What the server writes goes up as it is written; its end closes
        the connection, which ends the relay's read of it."""
        while True:
            try:
                chunk = server.process.stdout.read(65536)
            except (OSError, ValueError):
                chunk = b""
            if not chunk:
                break
            try:
                connection.sendall(chunk)
            except OSError:
                break
        with contextlib.suppress(OSError):
            connection.shutdown(socket.SHUT_RDWR)
