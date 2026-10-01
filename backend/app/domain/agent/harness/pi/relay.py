"""后台命令在执行机上的那一头：它拿着 pty，把终端上的字写到自己的输出里。

pi has no background shell and says so (`docs/usage.md`): its bash tool runs a
command to completion and hands back the output. A room needs otherwise — a dev
server, a training run, a long test suite, a debugger somebody wants to type
into — and a turn that blocks for twenty minutes is a turn nobody can talk to.

The command runs on the room's machine, as one of its executor's commands
(`jobs.py`): the executor keeps what this prints in its own files and reads it
back from any offset, and the session's runner copies it to the session host.
So a job outlives pi and the runner both; what it does not outlive is nobody
reading it for longer than the executor waits for an abandoned command.

**Why a pty at all, rather than pipes.** Two things pipes cannot do. Programs
ask `isatty` and go line-buffered or fully-buffered on the answer, so over a
pipe a server's "listening on :3000" can sit in its buffer for an hour. And a
REPL — python, psql, gdb — only offers a prompt to a terminal. The pty is what
makes typing into a job meaningful rather than a second way to say `run`.

What is typed arrives through a named pipe beside the job (``fifo``, the first
argument), opened read-write here so a writer never blocks and the terminal
never sees an end of input between two writes.

Run on the machine as a script (`python3 - <fifo> <cwd> <command>`, its source
on stdin): standard library only, importing nothing of ours. Its exit status is
the command's, or 128 plus the signal that ended it.
"""

from __future__ import annotations

import errno
import fcntl
import os
import select
import signal
import struct
import sys
import termios

# A terminal wide enough that a log line, a stack trace or a table is not folded
# into unreadability. Programs lay themselves out against this and cannot ask a
# reader what would suit them.
COLUMNS, ROWS = 200, 50

# One read from the pty. Large enough to keep up with a noisy build without
# spinning, small enough to interleave with what is typed.
CHUNK = 65536


def _spawn(command: str, cwd: str) -> tuple[int, int]:
    """The command, on the far side of a new terminal."""
    master, slave = os.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLUMNS, 0, 0))
    child = os.fork()
    if child == 0:
        # The child is a copy of this relay until exec replaces it, and must
        # never return into it. What went wrong goes to the terminal, where a
        # shell's own complaint would have gone.
        try:
            os.close(master)
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
            for target in (0, 1, 2):
                os.dup2(slave, target)
            if slave > 2:
                os.close(slave)
            os.chdir(cwd)
            # `dumb`, and this is the single most consequential line here. The
            # reader is a language model reading a transcript, not eyes
            # watching a screen. Told it has a capable terminal, Python's REPL,
            # psql and most build tools redraw the current line on every
            # keystroke — measured 2026-09-17, `print(6*7)` typed into
            # `python3 -i` came back as two kilobytes of cursor motion with the
            # `42` buried inside it.
            environment = {**os.environ, "TERM": "dumb"}
            environment["COLUMNS"], environment["LINES"] = str(COLUMNS), str(ROWS)
            # Not a login shell: this relay was started in the user's shell
            # after their profile (`jobs.py`), and a login shell would read
            # /etc/profile again over what that put on PATH.
            os.execvpe("/bin/sh", ["/bin/sh", "-c", command], environment)
        except BaseException as error:  # noqa: BLE001 — nothing may escape
            try:
                os.write(2, f"{type(error).__name__}: {error}\n".encode())
            except OSError:
                pass
        os._exit(127)
    os.close(slave)
    return master, child


def _status(raw: int) -> int:
    return 128 + os.WTERMSIG(raw) if os.WIFSIGNALED(raw) else os.WEXITSTATUS(raw)


def main(fifo: str, cwd: str, command: str) -> int:
    try:
        os.unlink(fifo)
    except FileNotFoundError:
        pass
    os.mkfifo(fifo, 0o600)
    keyboard = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
    master, child = _spawn(command, cwd)

    def forward(number, _frame):
        # The terminal's group, not this relay: the shell's child is what is
        # actually running, and stopping only the relay leaves it behind.
        try:
            os.killpg(child, number)
        except OSError:
            pass

    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(number, forward)
    status = None
    try:
        while status is None:
            try:
                readable, _, _ = select.select([master, keyboard], [], [], 0.5)
            except InterruptedError:
                continue
            if keyboard in readable:
                try:
                    os.write(master, os.read(keyboard, CHUNK))
                except BlockingIOError:
                    pass
            if master in readable:
                try:
                    data = os.read(master, CHUNK)
                except OSError as error:
                    # The far side closed the terminal: EIO is how a pty says so.
                    if error.errno != errno.EIO:
                        raise
                    data = b""
                if data:
                    os.write(1, data)
                    continue
                status = _status(os.waitpid(child, 0)[1])
                break
            waited, raw = os.waitpid(child, os.WNOHANG)
            if waited:
                # Drain first: what the command printed just before exiting is
                # usually the only part anybody wanted.
                while True:
                    try:
                        data = os.read(master, CHUNK)
                    except OSError:
                        break
                    if not data:
                        break
                    os.write(1, data)
                status = _status(raw)
    finally:
        try:
            os.unlink(fifo)
        except OSError:
            pass
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3]))
