"""A stand-in for the ``docker`` command, for the judge runner's tests.

It understands what the runner asks of docker and nothing more: ``run`` executes
the command after the image with ``sh -c`` on this machine, in the bind-mounted
directory, and reports the way GNU time and ``docker inspect`` would; ``kill``
stops it; ``inspect``, ``rm``, ``image inspect`` and ``pull`` answer from a
state directory. None of the container's limits are real here — the real ones
are held by ``scripts/remote_execution/judge_acceptance.py`` on a real docker.

A command containing ``__oom__`` ends as the kernel's memory limit would end
it: killed, with ``OOMKilled`` set.

The state directory is ``$FAKE_DOCKER_STATE``.
"""

from __future__ import annotations

import json
import os
import resource
import signal
import subprocess
import sys
import time
from pathlib import Path

STATE = Path(os.environ["FAKE_DOCKER_STATE"])
TIME_PROGRAM = "/usr/bin/time"


def _option(args: list[str], flag: str) -> str | None:
    for i, arg in enumerate(args):
        if arg == flag and i + 1 < len(args):
            return args[i + 1]
    return None


def _run(args: list[str]) -> int:
    name = _option(args, "--name") or "unnamed"
    mount = _option(args, "--mount") or ""
    fields = dict(part.split("=", 1) for part in mount.split(",") if "=" in part)
    source, target = fields.get("source", "."), fields.get("target", "/code")
    workdir = (_option(args, "--workdir") or target).replace(target, source, 1)
    at = args.index(TIME_PROGRAM)
    time_format = args[at + 2]
    command = args[at + 3 :]
    started = time.monotonic()
    if "__oom__" in " ".join(command):
        sys.stdin.read()
        (STATE / f"{name}.json").write_text(
            json.dumps({"OOMKilled": True, "ExitCode": 137})
        )
        return 137
    child = subprocess.Popen(command, cwd=workdir, start_new_session=True)
    (STATE / f"{name}.pid").write_text(str(child.pid))
    _, status, usage = os.wait4(child.pid, 0)
    code = os.waitstatus_to_exitcode(status)
    code = 128 - code if code < 0 else code
    line = (
        time_format.replace("%e", f"{time.monotonic() - started:.2f}")
        .replace("%U", f"{usage.ru_utime:.2f}")
        .replace("%S", f"{usage.ru_stime:.2f}")
        .replace("%M", str(usage.ru_maxrss))
    )
    sys.stderr.write(line + "\n")
    (STATE / f"{name}.json").write_text(
        json.dumps({"OOMKilled": False, "ExitCode": code})
    )
    return code


def _kill(name: str) -> int:
    pid_file = STATE / f"{name}.pid"
    if pid_file.exists():
        try:
            os.killpg(int(pid_file.read_text()), signal.SIGKILL)
        except ProcessLookupError:
            pass
    return 0


def main(args: list[str]) -> int:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if args[:1] == ["run"]:
        return _run(args[1:])
    if args[:1] == ["kill"]:
        return _kill(args[1])
    if args[:2] in (["image", "inspect"],) or args[:1] == ["pull"]:
        return 0
    if args[:1] == ["inspect"]:
        state = STATE / f"{args[-1]}.json"
        if not state.exists():
            sys.stderr.write(f"Error: No such object: {args[-1]}\n")
            return 1
        sys.stdout.write(state.read_text())
        return 0
    if args[:1] == ["rm"]:
        for suffix in (".json", ".pid"):
            (STATE / f"{args[-1]}{suffix}").unlink(missing_ok=True)
        return 0
    sys.stderr.write(f"fake docker: unexpected call {args}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
