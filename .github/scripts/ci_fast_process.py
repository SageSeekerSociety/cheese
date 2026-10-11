"""Bound local checks on Linux; sample RSS of only the group we create.

This is not an address-space limit. RSS sampling can miss short peaks, and
children that deliberately create another session are outside this group.
The work machine's per-process cap remains the hard limit.
"""

import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time


def group_rss(pgid, timeout):
    proc = subprocess.run(
        ["ps", "-eo", "pgid=,rss="], capture_output=True, text=True, timeout=timeout
    )
    if proc.returncode:
        raise OSError("ps could not measure the check's process group")
    return sum(
        int(fields[1]) for line in proc.stdout.splitlines()
        if len(fields := line.split()) == 2 and fields[0] == str(pgid)
    ) / 1024


def stop_group(proc):
    # No pre-existing service belongs to the new session/process group.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()


def execute(command, cwd, timeout, memory_mib=3072, env=None):
    start = time.monotonic()
    result = {"command": command, "result": "unknown", "seconds": 0, "peak_rss_mib": 0}
    if timeout <= 0:
        return {**result, "result": "budget", "output": "No time left in this tier."}
    if not hasattr(os, "killpg"):
        return {**result, "result": "blocked", "output": "Bounded ci:fast needs Linux ps/process groups."}
    with tempfile.TemporaryFile() as output:
        try:
            proc = subprocess.Popen(
                command, cwd=cwd, env=env, stdout=output, stderr=output,
                start_new_session=True,
            )
        except OSError as exc:
            return {**result, "result": "blocked", "output": str(exc)}
        try:
            while True:
                remaining = timeout - (time.monotonic() - start)
                if remaining <= 0:
                    result["result"] = "timeout"
                    break
                rss = group_rss(proc.pid, min(1, remaining))
                result["peak_rss_mib"] = max(result["peak_rss_mib"], round(rss, 1))
                if rss >= memory_mib:
                    result["result"] = "memory"
                    break
                if time.monotonic() - start >= timeout:
                    result["result"] = "timeout"
                    break
                if proc.poll() is not None:
                    result["returncode"] = proc.returncode
                    # Shell wrappers may translate SIGKILL/SIGTERM to 137/143.
                    result["result"] = (
                        "unknown" if proc.returncode < 0 or proc.returncode in (137, 143) else
                        "pass" if proc.returncode == 0 else "fail"
                    )
                    break
                time.sleep(min(0.1, max(0, timeout - (time.monotonic() - start))))
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
