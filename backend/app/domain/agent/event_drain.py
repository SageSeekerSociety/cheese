"""Deliver a device's durable hook spool without spawning idle polling processes."""

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

# How long a refused spool waits between passes: one second at first, then
# doubling to a minute. An event the platform can never accept is bounded by
# this and nothing else — the forwarder now refuses to spool the empty body that
# produced one, but a spool already holding such a file still has to stop
# costing 58 requests a minute forever.
RETRY_MIN_S = 1.0
RETRY_MAX_S = 60.0


class Delivery(NamedTuple):
    """What one pass over the spool achieved."""

    delivered: int
    rejected: int


def deliver_events(spool: Path, values: dict) -> Delivery:
    delivered = rejected = 0
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
                result.returncode == 0 and json.loads(result.stdout).get("code") == 200
            )
        except (ValueError, AttributeError):
            acknowledged = False
        if acknowledged:
            event.unlink(missing_ok=True)
            delivered += 1
        else:
            rejected += 1
    return Delivery(delivered, rejected)


def main(script: Path) -> None:
    script.with_suffix(script.suffix + ".pid").write_text(str(os.getpid()))
    Path(str(script) + ".version").write_text(
        subprocess.check_output(["cksum", str(script)], text=True).split()[0]
    )
    config = Path(str(script) + ".env")
    tether = os.environ.get("CHEESE_DRAIN_TETHER")
    next_prune = 0.0
    backoff = RETRY_MIN_S
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
        outcome = deliver_events(spool, values)
        failed = bool(outcome.rejected)
        if time.monotonic() >= next_prune:
            cutoff = time.time() - 86400
            for pattern in (".n[0-9]*",):
                for event in spool.glob(pattern):
                    try:
                        if event.is_file() and event.stat().st_mtime < cutoff:
                            event.unlink(missing_ok=True)
                    except FileNotFoundError:
                        pass
            next_prune = time.monotonic() + 60
        # A pass that delivered nothing and was refused everything waits longer
        # each time, up to RETRY_MAX_S. Retrying fast buys only a faster recovery
        # once the far end works again; it costs unbounded load when the far end
        # is never going to accept this spool — and some events never will be.
        # One zero-byte event, which the backend answers `hook body must be a
        # JSON object`, cost 12,658 requests in a day at the flat one-second
        # retry this replaces (2026-09-16): a tenth of everything the platform
        # served, for one file that could not be accepted at any rate.
        #
        # Progress resets it, so one poison event cannot slow down the events
        # behind it: what backs off is a pass that moved nothing at all.
        # Nothing is ever dropped — an unacknowledged event stays on disk,
        # because until this loop is acknowledged the only copy is here.
        if outcome.delivered:
            backoff = RETRY_MIN_S
        if failed:
            time.sleep(backoff)
            if backoff < RETRY_MAX_S:
                backoff = min(backoff * 2, RETRY_MAX_S)
                if backoff >= RETRY_MAX_S:
                    with Path(str(script) + ".log").open("a") as log:
                        log.write(
                            f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} "
                            f"hook delivery refused {outcome.rejected} event(s); "
                            f"retrying every {RETRY_MAX_S:.0f}s until it is accepted\n"
                        )
        else:
            backoff = RETRY_MIN_S
            time.sleep(0.1)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
