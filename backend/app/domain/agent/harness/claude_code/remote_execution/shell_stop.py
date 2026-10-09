"""How a command the build started through the shell prefix is stopped.

The build follows a stop with a kill of the prefix about a second later, and a
command whose start was still on its way would otherwise run on with nobody
left to stop it. So a watcher that outlives the prefix delivers the stop: a
fork on POSIX, on Windows this program again (`client.py watch`), started out
of the prefix's process tree. Shipped beside `client.py` (`release.sources`).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import runpy
import select
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

if __package__:
    from app.domain.agent.executor_transport import RemoteClient
else:
    from executor_transport import RemoteClient

# After a stop reaches the executor, how long the command gets to end before
# it is killed outright.
SHELL_STOP_GRACE_S = 5.0

# The stops a command is sent, by the POSIX numbers they travel under to the
# executor (`runtime.STOP_SIGNALS`), whatever this machine calls them.
TERM, KILL = 15, 9
# The ones this process can be sent here: Windows has no SIGHUP.
CAUGHT = tuple(
    getattr(signal, name)
    for name in ("SIGTERM", "SIGINT", "SIGHUP")
    if hasattr(signal, name)
)


def portable():
    """The Windows primitives shipped beside this file, as runtime.py loads them."""

    return runpy.run_path(str(Path(__file__).with_name("portable.py")))


def current_target(target):
    """Where the session's commands go now. A session started before its
    machine was rented holds a placeholder until the lease names the machine
    (`RemoteClient.call`), which writes the target file anew; a process that
    read the placeholder before then reads the machine from there."""
    with contextlib.suppress(KeyError, OSError, ValueError):
        return json.loads(Path(target["target_file"]).read_text())
    return target


def start_watcher(target, command_id):
    """Fork the process that stops the command when this one cannot; the
    write end of its pipe. It is detached before the command is started, so
    the build's kill of this process and its children never reaches it.

    Windows has no fork: the watcher is this program again (`watch`), started
    out of this process's tree, reading its stdin."""
    if sys.platform == "win32":
        watcher = portable()["popen_daemon"](
            [
                sys.executable,
                str(Path(__file__).with_name("client.py").resolve()),
                "watch",
                target["target_file"],
                command_id,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return watcher.stdin.fileno()
    read_end, write_end = os.pipe()
    first = os.fork()
    if first == 0:
        try:
            os.setsid()
            if os.fork() == 0:
                os.close(write_end)
                # Nothing of the build's: holding its output file or a hook's
                # pipes open would keep the call from ending.
                quiet = os.open(os.devnull, os.O_RDWR)
                for fd in (0, 1, 2):
                    os.dup2(quiet, fd)
                for number in CAUGHT:
                    signal.signal(number, signal.SIG_IGN)
                deliver_stop(target, command_id, read_end)
        finally:
            os._exit(0)
    os.close(read_end)
    os.waitpid(first, 0)
    return write_end


def deliver_stop(target, command_id, pipe):
    """Wait on the prefix; stop its command unless it says it has ended.

    `stop <n>` or the pipe closing without `done` stops it: signal n (TERM
    when the prefix was killed), then KILL after the grace if it still runs.
    A stop the executor gets before the start is kept there, and the start is
    refused (`runtime.py` `shell`). Delivery is retried across a dropped link
    for as long as the executor would keep the command for its reader.
    """
    received, number = b"", None
    while number is None:
        chunk = os.read(pipe, 256)
        received += chunk
        if b"done" in received:
            return
        found = re.search(rb"stop (\d+)", received)
        if found:
            number = int(found.group(1))
        elif not chunk:
            number = TERM
    if current_target(target).get("kind") == "unavailable":
        return  # a session with no machine started nothing there
    client = RemoteClient(current_target(target))

    def send(signalled):
        started = time.monotonic()
        nonlocal client
        while time.monotonic() < started + 600:
            try:
                return client.call(
                    "control",
                    {
                        "subtype": "shell",
                        "operation": "signal",
                        "command_id": command_id,
                        "signal": int(signalled),
                    },
                )
            except Exception:  # noqa: BLE001 — link down or refused: retry, backing off
                client = RemoteClient(current_target(target))
                time.sleep(min(30.0, max(1.0, time.monotonic() - started)))

    send(number)
    if b"done" in received + read_within(pipe, SHELL_STOP_GRACE_S):
        return
    send(KILL)


def read_within(pipe, seconds):
    """What the pipe gives within `seconds`, or nothing. A pipe cannot be
    selected on Windows, so there a thread waits on the read."""
    if sys.platform != "win32":
        ready = select.select([pipe], [], [], seconds)[0]
        return os.read(pipe, 256) if ready else b""

    got = []
    reader = threading.Thread(
        target=lambda: got.append(os.read(pipe, 256)), daemon=True
    )
    reader.start()
    reader.join(seconds)
    return got[0] if got else b""
