"""Bound local checks on Linux; sample RSS of only the group we create.

This is not an address-space limit. RSS sampling can miss short peaks, and
children that deliberately create another session are outside this group. The
work machine's per-process cap remains the hard limit.

A check gets its own process group, a bounded wall clock, a readable output
tail, and an environment scrubbed of the variables that would change its
verdict: pre-commit's own SKIP/colour overrides and stray git/pytest
redirection. PATH/HOME are kept — they are how the checks find their runtimes.
"""

import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

# Vars that alter what a check does rather than where its tools live. A test
# harness (or a shell one forgot to clean) must not be able to turn a real
# check green through them.
SCRUB = (
    "SKIP", "PRE_COMMIT_COLOR",
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG", "GIT_CONFIG_GLOBAL",
    "PYTEST_ADDOPTS", "NODE_OPTIONS",
)


def environment(extra=None):
    """The caller's environment, minus the verdict-altering variables."""
    env = {k: v for k, v in os.environ.items() if k not in SCRUB}
    env.update(extra or {})
    return env


class MissingPs(OSError):
    """`ps` is not on this machine. The tier cannot bound memory here at all."""


class Unmeasured(OSError):
    """One sample was missed. Says nothing about the machine."""


# The sampler's own window, deliberately not the check's remaining time. Tied to
# a nearly spent budget it shrinks to milliseconds, and a machine with anything
# else running on it then reports the sample as a miss — which used to read as
# "this machine has no ps" and turned an expected exit 2 (unknown) into blocked's
# 3, twice on #3476 on 2026-10-12.
MEASURE_TIMEOUT = 1.0
# Missed samples *in a row* to ride out before giving up on measuring a check.
# Once given up, that check is judged by its exit code and the clock alone.
MEASURE_ATTEMPTS = 3


def group_rss(pgid, timeout=MEASURE_TIMEOUT):
    for argv in (["ps", "-eo", "pgid=,rss="], ["ps", "-o", "pgid=,rss="]):
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            # ps itself is absent: a real "cannot measure on this machine".
            raise MissingPs("ps is not installed")
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0:
            return sum(
                int(fields[1]) for line in proc.stdout.splitlines()
                if len(fields := line.split()) == 2 and fields[0] == str(pgid)
            ) / 1024
    raise Unmeasured("ps could not measure the check's process group")


def stop_group(proc):
    # No pre-existing service belongs to the new session/process group.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()


def execute(command, cwd, timeout, memory_mib=3072, env=None):
    start = time.monotonic()
    result = {"command": command, "result": "unknown", "seconds": 0,
              "peak_rss_mib": 0, "unmeasured": False}
    if timeout <= 0:
        return {**result, "result": "budget", "output": "No time left in this tier."}
    if not hasattr(os, "killpg"):
        return {**result, "result": "blocked", "output": "Bounded ci:fast needs Linux ps/process groups."}
    with tempfile.TemporaryFile() as output:
        try:
            proc = subprocess.Popen(
                command, cwd=cwd, env=environment(env), stdout=output, stderr=output,
                start_new_session=True,
            )
        except OSError as exc:
            return {**result, "result": "blocked", "output": str(exc)}
        try:
            misses = 0
            while True:
                remaining = timeout - (time.monotonic() - start)
                if remaining <= 0:
                    result["result"] = "timeout"
                    break
                if misses < MEASURE_ATTEMPTS:
                    try:
                        rss = group_rss(proc.pid)
                    except Unmeasured:
                        # A missed sample is not a verdict. Ride a couple out,
                        # then stop measuring and let the exit code and the clock
                        # decide; the fix this replaces reported the miss as
                        # blocked, i.e. "this machine lacks ps".
                        misses += 1
                        if misses >= MEASURE_ATTEMPTS:
                            result["unmeasured"] = True
                    else:
                        # A sample that landed clears the misses, so the ceiling
                        # goes on being enforced for the rest of the check: only
                        # MEASURE_ATTEMPTS misses *in a row* stop the measuring.
                        misses = 0
                        result["peak_rss_mib"] = max(result["peak_rss_mib"], round(rss, 1))
                        if rss >= memory_mib:
                            result["result"] = "memory"
                            break
                if proc.poll() is not None:
                    result["returncode"] = proc.returncode
                    # Shell wrappers may translate SIGKILL/SIGTERM to 137/143.
                    result["result"] = (
                        "unknown" if proc.returncode < 0 or proc.returncode in (137, 143) else
                        "pass" if proc.returncode == 0 else "fail"
                    )
                    break
                time.sleep(min(0.1, max(0, remaining)))
        except subprocess.TimeoutExpired:
            result["result"] = "timeout"
        except OSError as exc:
            result["result"] = "blocked"
            result["reason"] = str(exc)
        finally:
            # Also reap children left behind by an otherwise successful wrapper.
            stop_group(proc)
        output.seek(0, os.SEEK_END)
        size = output.tell()
        output.seek(max(0, size - 1024 * 1024))
        result["output"] = output.read().decode(errors="replace")
        if size > 1024 * 1024:
            result["output"] = "[only the last 1 MiB of output is retained]\n" + result["output"]
    result["seconds"] = round(time.monotonic() - start, 2)
    return result


def dependencies(root, scope):
    missing = []
    if scope.get("backend") and not Path(root, "backend/.venv").is_dir():
        missing.append("backend/.venv 不存在 — task be:deps:sync / (cd backend && uv sync)")
    if scope.get("frontend") and not Path(root, "frontend/node_modules").is_dir():
        missing.append("frontend/node_modules 不存在 — task fe:install / (cd frontend && pnpm install --frozen-lockfile)")
    return missing
