"""后台命令的看守进程：它拿着 pty，所以 pi 走了命令还在。

pi has no background shell and says so (`docs/usage.md`): its bash tool runs a
command to completion and hands back the output. A room needs otherwise — a dev
server, a training run, a long test suite, a debugger somebody wants to type
into — and a turn that blocks for twenty minutes is a turn nobody can talk to.

**Why a separate process and not a library inside pi.** A job has to outlive pi:
the session process is replaced by a deploy, by a crash, by a model switch, and
a build that dies with it is a build that has to start over. Whoever holds the
pty master decides that — when the last master descriptor closes, the kernel
sends SIGHUP to the foreground process group of that terminal. So the master
cannot live in pi, in any language. It lives here, in its own session, and what
pi holds is a path.

**Why a pty at all, rather than pipes.** Two things pipes cannot do. Programs
ask `isatty` and go line-buffered or fully-buffered on the answer, so over a
pipe a server's "listening on :3000" can sit in its buffer for an hour. And a
REPL — python, psql, gdb — only offers a prompt to a terminal. The pty is what
makes `write` meaningful rather than a second way to say `run`.

**What the machine is asked for.** python3 and nothing else: it is what the
runner itself is written in, so a machine that can host a pi room can run this.

State is entirely in the filesystem, under one directory per job, because the
reader is a process that did not start it and may not have existed when it did:

    meta.json   what was run, where, by whom, when, and where to reach it
    output      everything the terminal has shown      (append-only)
    cursor      how far a reader has got               (the reader's)
    exit        status and when                        (written once, last)
    error       why nothing else here happened         (only when that is so)

The control socket is the one thing NOT in that directory; see ``_address``.
"""

import argparse
import errno
import fcntl
import hashlib
import json
import os
import pty
import select
import socket
import struct
import sys
import termios
import time
import traceback
from pathlib import Path

# A terminal wide enough that a log line, a stack trace or a table is not folded
# into unreadability. Programs lay themselves out against this and cannot ask a
# reader what would suit them.
COLUMNS, ROWS = 200, 50

# One read from the pty. Large enough to keep up with a noisy build without
# spinning, small enough to interleave with the control socket.
CHUNK = 65536


def _sizes(descriptor: int) -> None:
    fcntl.ioctl(
        descriptor, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLUMNS, 0, 0)
    )


def _daemonize(directory: Path) -> None:
    """Leave the caller's session for one of our own.

    Twice-forked and `setsid` between, which is what makes the pty we are about
    to open OUR controlling terminal rather than an inherited one — and what
    keeps the job out of the process group pi is killed by.
    """
    if os.fork():
        os._exit(0)
    os.setsid()
    if os.fork():
        os._exit(0)
    os.chdir("/")
    null = os.open(os.devnull, os.O_RDWR)
    for target in (0, 1, 2):
        os.dup2(null, target)
    if null > 2:
        os.close(null)


def _spawn(command: str, cwd: str, environ: dict) -> tuple[int, int]:
    """The command, on the far side of a new terminal."""
    master, slave = pty.openpty()
    _sizes(slave)
    child = os.fork()
    if child == 0:
        os.close(master)
        os.setsid()
        fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
        for target in (0, 1, 2):
            os.dup2(slave, target)
        if slave > 2:
            os.close(slave)
        os.chdir(cwd)
        # `dumb`, and this is the single most consequential line here. The
        # reader is a language model reading a transcript, not eyes watching a
        # screen. Told it has a capable terminal, Python's REPL, psql and most
        # build tools redraw the current line on every keystroke — measured
        # 2026-09-17, `print(6*7)` typed into `python3 -i` came back as two
        # kilobytes of cursor motion with the `42` buried inside it. `dumb`
        # asks all of them for the line-oriented behaviour instead, which is
        # the only kind that survives being read later.
        environment = {**os.environ, **environ, "TERM": "dumb"}
        environment["COLUMNS"], environment["LINES"] = str(COLUMNS), str(ROWS)
        os.execvpe("/bin/sh", ["/bin/sh", "-lc", command], environment)
        os._exit(127)  # unreachable unless exec itself failed
    os.close(slave)
    return master, child


#: 控制口不在任务目录里 —— 那里放不下它。
#:
#: A Unix socket address is 108 bytes, terminator included, and that is a kernel
#: constant rather than a filesystem limit: `bind` on a longer path fails, and
#: every other file in the job directory is written happily at any length. A
#: room's state directory alone is past it — a project id, a topic id and a
#: session digest, none of which this module is in a position to shorten — so a
#: socket in the job directory is not "long on some machines", it is a job that
#: cannot be typed into or signalled on any machine.
#:
#: So the address is short by construction, and the job directory carries the
#: name in ``meta.json`` rather than the other side deriving it a second time.
#: The runner's own socket is named the same way for the same reason
#: (``runner.socket_path``); the uid is in the name because /tmp is shared.
_CONTROL_ROOT = Path("/tmp")


def _address(directory: Path) -> str:
    digest = hashlib.sha256(str(directory).encode()).hexdigest()[:24]
    return str(_CONTROL_ROOT / f"cheese-bg-{os.getuid()}-{digest}.sock")


def _control(address: str) -> socket.socket:
    Path(address).unlink(missing_ok=True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(address)
    os.chmod(address, 0o600)
    listener.listen(8)
    listener.setblocking(False)
    return listener


def _serve(directory: Path, master: int, child: int, listener: socket.socket) -> int:
    output = (directory / "output").open("ab", buffering=0)
    ended = None
    while True:
        try:
            readable, _, _ = select.select([master, listener], [], [], 0.5)
        except InterruptedError:
            continue
        if listener in readable:
            _obey(listener, master, child)
        if master in readable:
            try:
                data = os.read(master, CHUNK)
            except OSError as error:
                # The far side closed the terminal: EIO is how a pty says that.
                if error.errno != errno.EIO:
                    raise
                data = b""
            if not data:
                ended = _reap(child)
                break
            output.write(data)
        if ended is None:
            waited, status = os.waitpid(child, os.WNOHANG)
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
                    output.write(data)
                ended = _status(status)
                break
    output.close()
    return ended if ended is not None else 0


def _reap(child: int) -> int:
    try:
        return _status(os.waitpid(child, 0)[1])
    except ChildProcessError:
        return 0


def _status(raw: int) -> int:
    return -os.WTERMSIG(raw) if os.WIFSIGNALED(raw) else os.WEXITSTATUS(raw)


def _obey(listener: socket.socket, master: int, child: int) -> None:
    """One request from whoever is driving this job. Never fatal.

    A malformed line, a reader that hung up, a signal to a process that has
    already gone — none of those are reasons to stop holding a terminal that is
    still producing output.
    """
    try:
        connection, _ = listener.accept()
    except OSError:
        return
    try:
        connection.settimeout(2)
        request = json.loads(connection.recv(1 << 20) or b"{}")
        if "write" in request:
            os.write(master, request["write"].encode())
        if "signal" in request:
            # The group, not the process: a shell's child is what is actually
            # running, and signalling only the shell leaves it behind.
            os.killpg(os.getpgid(child), int(request["signal"]))
        answer = {"ok": True}
    except Exception as error:  # noqa: BLE001 — an answer is the whole job here
        answer = {"ok": False, "error": f"{type(error).__name__}: {error}"}
    try:
        connection.sendall(json.dumps(answer, ensure_ascii=False).encode() + b"\n")
    except OSError:
        pass
    finally:
        connection.close()


def _hold(args: argparse.Namespace) -> None:
    """One job, from its terminal to its exit status.

    The control socket is bound BEFORE the command is started. Bound after, a
    guardian that cannot listen has already put a command on a pty nobody can
    reach — running, unreadable, and unkillable except by pid.
    """
    address = _address(args.dir)
    listener = _control(address)
    try:
        master, child = _spawn(args.command, args.cwd, {})
        (args.dir / "meta.json").write_text(
            json.dumps(
                {
                    "command": args.command,
                    "cwd": args.cwd,
                    "label": args.label,
                    "pid": os.getpid(),
                    "child": child,
                    "sock": address,
                    "started_at": time.time(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        try:
            status = _serve(args.dir, master, child, listener)
        finally:
            os.close(master)
    finally:
        listener.close()
        Path(address).unlink(missing_ok=True)
    # Written last and once: its presence is what "finished" means to a reader,
    # so it must not appear before the output it belongs to is on disk.
    (args.dir / "exit").write_text(
        json.dumps({"status": status, "at": time.time()}), encoding="utf-8"
    )


#: 看守进程自己死掉时的退出码，和命令的退出码取自同一个字段，所以要能分得开。
#: ``sysexits.h`` 的 EX_SOFTWARE：一条命令几乎不会拿它当退出码。
_GUARDIAN_FAILED = 70


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="hold one backgrounded command")
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--label", default="")
    parser.add_argument("--command", required=True)
    args = parser.parse_args(argv)
    args.dir.mkdir(parents=True, exist_ok=True)
    _daemonize(args.dir)
    try:
        _hold(args)
    except BaseException:
        # Past `_daemonize` this process has no voice: stdin, stdout and stderr
        # are /dev/null, the caller stopped waiting long ago, and the reader is
        # a model in another process that only ever sees these files. An
        # exception left to propagate here is a job that reports itself running
        # forever, having printed nothing, for a reason nobody can recover —
        # which is the same silence as no failure at all.
        (args.dir / "error").write_text(traceback.format_exc(), encoding="utf-8")
        exit_file = args.dir / "exit"
        if not exit_file.exists():
            exit_file.write_text(
                json.dumps({"status": _GUARDIAN_FAILED, "at": time.time()}),
                encoding="utf-8",
            )
        raise


if __name__ == "__main__":
    main(sys.argv[1:])
