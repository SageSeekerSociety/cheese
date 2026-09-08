"""Deliver a device's durable hook spool without spawning idle polling processes."""

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path


def main(script: Path) -> None:
    script.with_suffix(script.suffix + ".pid").write_text(str(os.getpid()))
    config = Path(str(script) + ".env")
    tether = os.environ.get("CHEESE_DRAIN_TETHER")
    next_prune = 0.0
    while True:
        if tether:
            try:
                os.kill(int(tether), 0)
            except ProcessLookupError:
                return
        if not config.exists():
            time.sleep(5)
            continue
        values = dict(entry.split("=", 1) for entry in shlex.split(config.read_text()))
        if not values.get("CHEESE_HOOK_SPOOL"):
            time.sleep(5)
            continue
        spool = Path(values["CHEESE_HOOK_SPOOL"])
        failed = False
        for event in sorted(spool.glob("[0-9]*")):
            # Keep curl's deployed proxy/TLS behavior. It runs only for an event,
            # while Python's sleep and file checks require no child processes.
            result = subprocess.run(
                [
                    "curl",
                    "-s",
                    "-m",
                    "10",
                    "-X",
                    "POST",
                    "-H",
                    "Content-Type: application/json",
                    "-H",
                    "X-Cheese-Token: " + values.get("CHEESE_TOKEN", ""),
                    "-H",
                    "X-Cheese-Event-Id: " + event.name.rsplit(".", 1)[-1],
                    "--data-binary",
                    "@" + str(event),
                    values.get("CHEESE_HOOK_URL", ""),
                ],
                capture_output=True,
                check=False,
            )
            try:
                acknowledged = (
                    result.returncode == 0
                    and json.loads(result.stdout).get("code") == 200
                )
            except (ValueError, AttributeError):
                acknowledged = False
            if acknowledged:
                event.unlink(missing_ok=True)
            else:
                failed = True
        if time.monotonic() >= next_prune:
            cutoff = time.time() - 86400
            for pattern in ("[0-9]*", ".n[0-9]*"):
                for event in spool.glob(pattern):
                    try:
                        if event.is_file() and event.stat().st_mtime < cutoff:
                            event.unlink(missing_ok=True)
                    except FileNotFoundError:
                        pass
            next_prune = time.monotonic() + 60
        # Keep the retry delay on delivery failure; only idle observation is faster.
        time.sleep(1 if failed else 0.1)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
