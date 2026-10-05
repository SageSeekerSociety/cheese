"""What an install does about the executor that already owns a room's state.

Shipped beside `runtime.py` (`RELEASE_FILES`), standard library only, and run in
the installing process: `bootstrap.py` loads it by path the way it loads
`portable` — that file arrives on stdin before any release is on disk, so it
cannot import a sibling, and neither can this one.

One executor serves a state at a time: `runtime.serve` holds `service.lock` for
as long as it runs and a second one cannot take it. An install that finds a
predecessor there has to end with one executor serving the room — never two, and
never an install that reads as a start that broke because a predecessor was
still there. That last one is what an install looks like when it skips the
predecessor because it did not answer: it installs over an occupied state, its
own executor dies on the lock, and the platform tells the room its executor
failed to start.
"""

import functools
import os
import runpy
import signal
import subprocess
import sys
import time
from pathlib import Path

# How long an install waits for a predecessor that owns the state's lock to
# publish its endpoint — `runtime.serve` writes that file only once it listens.
# Long enough for one that is on its way up on a loaded machine, short enough
# that a wedged one is not waited on for the room's whole readiness deadline.
PREDECESSOR_GRACE_S = 15

# How often the wait above looks again. Small: a predecessor that is coming up
# answers within a tick or two of writing its endpoint.
LOOK_AGAIN_S = 0.1


def held(state):
    """The descriptor an `executor_state` path names, for a child to keep.

    A copy of `bootstrap.held`, which cannot be imported from here: a sandboxed
    room's state is opened by its install and named through `/proc/self/fd`, and
    the stop below is asked of the machine's own interpreter, outside that
    install, so the descriptor has to be handed over explicitly.
    """
    if str(state).startswith("/proc/self/fd/"):
        return (int(Path(state).name),)
    return ()


def occupied(state, runtime):
    """Whether an executor is holding this state's lock right now.

    A lock nobody holds is what this install is about to take; a lock somebody
    holds is the one thing a second executor cannot take. Asked of the `runtime`
    already loaded from the release being installed, because this file arrives
    on stdin before any release is on disk and cannot load `portable.py` for the
    Windows lock itself (see `bootstrap.lock`).
    """
    with (state / "service.lock").open("a") as lock_file:
        try:
            runtime["lock"](lock_file, blocking=False)
        except BlockingIOError:
            return True
    return False


def await_predecessor(state, runtime, grace=PREDECESSOR_GRACE_S):
    """The answer of an executor that owns this state, waiting for one that is on
    its way up.

    Called where the state is held but nothing answered: a predecessor that is
    starting has taken the lock already and publishes its endpoint only once it
    listens, so this is not "no executor" — waiting for it and reusing it is
    what keeps the install from starting a second one over the same state. It
    has to exist by the end of the grace: an executor that owns the state and
    never answers is neither usable nor installable over, and saying so is the
    one honest outcome left.
    """
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        time.sleep(LOOK_AGAIN_S)
        try:
            return runtime["request"](state, "ping")
        except (ConnectionError, FileNotFoundError):
            continue
    raise RuntimeError(
        "This room's executor is running and does not answer; it has to stop "
        "before a release can be installed over its state"
    )


def stop_predecessor(state):
    """End the executor that owns `state`, asked with the release this module was
    loaded from — never with the programs an old root holds (see
    `bootstrap.stop_previous_root`).

    What is asked of it is requests every executor answers, and a stop that also
    ends one that stopped answering anything else (`runtime.terminate_unrequested`)
    — so this is how a predecessor that has gone silent is ended too. A stop that
    fails is a refusal of the room, not something to work around: two executors
    must not run over one home.
    """
    try:
        subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("runtime.py")),
                "stop",
                "--state",
                str(state),
            ],
            check=True,
            timeout=30,
            pass_fds=held(state),
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(
            f"The executor that owns {state} did not stop; a second one cannot take it"
        ) from exc


@functools.cache
def runtime_module():
    """The release's `runtime.py`, loaded by path the way this file was.

    Cached, and the handle everything else here goes through: `beside` below is
    `runtime.beside`, so a sibling is loaded one way in this release and not two.
    """
    return runpy.run_path(str(Path(__file__).with_name("runtime.py")))


def command_line(pid):
    """How this machine spells the command line of a process, or "".

    Asked of the machine's own table, because `ps` is not a Windows program:
    there `portable.processes()` reads the same thing of every process this
    user can see. One implementation, since the test below it is the only proof
    that a pid is the process this module means to end.
    """
    if sys.platform == "win32":
        rows = runtime_module()["beside"]("portable")["processes"]()
        return next((row["command"] for row in rows if row["pid"] == pid), "")
    return subprocess.run(
        ["ps", "-ww", "-p", str(pid), "-o", "args="],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.rstrip()


def serving(command, state, named):
    """Whether `command` is an executor serving this state, however it is spelled.

    The state has three spellings between the room's own record, the service
    started for it, and the path a sandbox's `/proc/self/fd/N` resolves to; on
    Windows an argument with a space in it arrives quoted.
    """
    tails = [
        spelling
        for path in {str(named), str(state), str(Path(state).resolve())}
        for spelling in (f"serve --state {path}", f'serve --state "{path}"')
    ]
    return any(command.endswith(tail) for tail in tails)


def end_unrequested(state, named):
    """End an executor that refused `shutdown`: one started before stopping was
    a request.

    Signal the pid it reports once its command line is seen running this state's
    service: whatever answers on a room's socket is not proof of who is behind
    it, and neither is a pid. The signal is this machine's own — a console-less
    process has no gentler request than `terminate_tree` on Windows (which takes
    the programs it started with it, as they are its children and not the
    room's), and SIGTERM elsewhere, which `serve` handles by shutting down.
    """
    try:
        pid = runtime_module()["request"](state, "ping")["pid"]
    except (OSError, RuntimeError):
        return
    if not serving(command_line(pid), state, named):
        return
    if sys.platform == "win32":
        runtime_module()["beside"]("portable")["terminate_tree"](pid)
    else:
        os.kill(pid, signal.SIGTERM)


def refuse_if_served(state, runtime):
    """Say that the state is held, when what just exited could not take it.

    Called after an executor this install started has exited: if the lock is
    held, that process could not take an occupied state rather than having
    failed to start, and the room's error has to say which of the two it was.
    """
    if occupied(state, runtime):
        raise RuntimeError(
            "This room's executor is running and does not answer; it has to stop "
            "before another one can serve the state"
        )
