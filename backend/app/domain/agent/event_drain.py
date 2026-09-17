"""Deliver a device's durable hook spool without spawning idle polling processes."""

import base64
import fcntl
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlencode

# How long a refused spool waits between passes: one second at first, then
# doubling to a minute. An event the platform can never accept is bounded by
# this and nothing else — the forwarder now refuses to spool the empty body that
# produced one, but a spool already holding such a file still has to stop
# costing 58 requests a minute forever.
RETRY_MIN_S = 1.0
RETRY_MAX_S = 60.0

CHUNK_BYTES = 1024 * 1024


def _save_progress(path: Path, state: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as output:
        json.dump(state, output)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _send_pending(
    values: dict, source: str, previous: dict, ledger: Path, state: dict
) -> None:
    pending = previous["pending"]
    content = base64.b64decode(pending["content"])
    offset = previous["offset"]
    base_url, topic = values["CHEESE_HOOK_URL"].rsplit("/hooks/", 1)
    query = urlencode({"source": source, "offset": offset})
    result = subprocess.run(
        [
            "curl",
            "-sS",
            "--fail",
            "-m",
            "30",
            "-X",
            "PUT",
            "-H",
            "X-Cheese-Token: " + values["CHEESE_TOKEN"],
            "-H",
            "Content-Type: application/octet-stream",
            "--data-binary",
            "@-",
            f"{base_url}/transcripts/{topic}/{previous['id']}?{query}",
        ],
        input=content,
        capture_output=True,
        check=False,
    )
    try:
        answer = json.loads(result.stdout)
        receipt = answer.get("data", {})
    except (ValueError, AttributeError):
        answer, receipt = {}, {}
    if (
        result.returncode
        or answer.get("code") != 200
        or receipt
        != {
            "offset": offset,
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    ):
        raise RuntimeError(f"transcript upload failed: source={source} offset={offset}")
    previous.update(offset=offset + len(content), sha256=pending["end_sha256"])
    previous.setdefault("chunks", []).append(receipt)
    previous.pop("pending")
    _save_progress(ledger, state)


def collect_transcripts(
    script: Path, values: dict, *, flush: bool = False
) -> list[dict]:
    """Send original bytes, preserving progress after each acknowledged chunk."""
    home = Path(values["CHEESE_HOOK_SPOOL"]).parent.parent
    base = home / ".claude/projects"
    if base.is_symlink() or not base.resolve().is_relative_to(home.resolve()):
        raise RuntimeError("transcript directory escapes its resource")
    ledger = Path(str(script) + ".transcripts.json")
    with Path(str(ledger) + ".lock").open("a") as lock:
        # A cleanup flush and the live sender share one cursor.
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(ledger.read_text()) if ledger.exists() else {}
        # Retry the exact bytes before reading a tail that may have grown.
        for source, previous in state.items():
            if previous.get("pending"):
                _send_pending(values, source, previous, ledger, state)
        receipts = []
        for path in sorted(base.rglob("*.jsonl")):
            if path.is_symlink() or not path.resolve().is_relative_to(base.resolve()):
                continue
            source = str(path.relative_to(home))
            previous = state.get(source)
            with path.open("rb") as original:
                stat = os.fstat(original.fileno())
                fingerprint = [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]
                if (
                    not flush
                    and previous
                    and previous.get("fingerprint") == fingerprint
                ):
                    continue
                offset = previous["offset"] if previous else 0
                digest = hashlib.sha256()
                remaining = offset
                while remaining:
                    part = original.read(min(CHUNK_BYTES, remaining))
                    if not part:
                        break
                    digest.update(part)
                    remaining -= len(part)
                if (
                    previous is None
                    or remaining
                    or digest.hexdigest() != previous["sha256"]
                    or previous.get("inode") != [stat.st_dev, stat.st_ino]
                ):
                    # A changed prefix is a new original file generation.
                    previous = {
                        "id": str(uuid.uuid4()),
                        "offset": 0,
                        "sha256": hashlib.sha256().hexdigest(),
                        "inode": [stat.st_dev, stat.st_ino],
                        "chunks": [],
                    }
                    state[source] = previous
                    _save_progress(ledger, state)
                    original.seek(0)
                    offset = 0
                    digest = hashlib.sha256()
                while content := original.read(CHUNK_BYTES):
                    digest.update(content)
                    previous["pending"] = {
                        "content": base64.b64encode(content).decode("ascii"),
                        "end_sha256": digest.hexdigest(),
                    }
                    _save_progress(ledger, state)
                    _send_pending(values, source, previous, ledger, state)
                    offset = previous["offset"]
                    if not flush:
                        break  # Event delivery stays responsive during a backlog.
                after = os.fstat(original.fileno())
                if offset == after.st_size and after.st_mtime_ns == stat.st_mtime_ns:
                    previous["fingerprint"] = fingerprint
                    _save_progress(ledger, state)
                    receipts.append(
                        {
                            "source": source,
                            "id": previous["id"],
                            "size": offset,
                            "sha256": digest.hexdigest(),
                        }
                    )
                elif flush:
                    raise RuntimeError(f"transcript writer is still active: {source}")
        if flush:
            if deliver_events(Path(values["CHEESE_HOOK_SPOOL"]), values).rejected:
                raise RuntimeError("hook events still await acknowledgement")
            base_url, topic = values["CHEESE_HOOK_URL"].rsplit("/hooks/", 1)
            for receipt in receipts:
                previous = state[receipt["source"]]
                operation = values.get("CHEESE_CLEANUP_ID", "manual")
                if previous.get("verification_operation") != operation:
                    previous["verification_operation"] = operation
                    previous["verified_offset"] = 0
                    _save_progress(ledger, state)
                chunks = previous["chunks"] or [
                    {"offset": 0, "size": 0, "sha256": hashlib.sha256().hexdigest()}
                ]
                for chunk in chunks:
                    end = chunk["offset"] + chunk["size"]
                    if chunk["size"] and end <= previous.get("verified_offset", 0):
                        continue
                    manifest = {
                        "id": receipt["id"],
                        "source": receipt["source"],
                        **chunk,
                    }
                    result = subprocess.run(
                        [
                            "curl",
                            "-sS",
                            "--fail",
                            "-m",
                            "120",
                            "-X",
                            "POST",
                            "-H",
                            "X-Cheese-Token: " + values["CHEESE_TOKEN"],
                            "-H",
                            "Content-Type: application/json",
                            "--data-binary",
                            "@-",
                            f"{base_url}/transcripts/{topic}/{receipt['id']}/confirm",
                        ],
                        input=json.dumps(manifest).encode(),
                        capture_output=True,
                        check=False,
                    )
                    try:
                        response = json.loads(result.stdout)
                    except ValueError:
                        response = {}
                    if (
                        result.returncode
                        or response.get("code") != 200
                        or response.get("data") != manifest
                    ):
                        raise RuntimeError(
                            f"transcript confirmation failed: {receipt['source']} "
                            f"offset={chunk['offset']}"
                        )
                    # Each request verifies one bounded immutable object. A killed
                    # flush resumes here instead of downloading a long file again.
                    previous["verified_offset"] = end
                    _save_progress(ledger, state)
        return receipts


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
    next_transcripts = 0.0
    backoff = RETRY_MIN_S
    collector = ThreadPoolExecutor(max_workers=1)
    collecting = None
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
        had_events = any(spool.glob("[0-9]*"))
        outcome = deliver_events(spool, values)
        failed = bool(outcome.rejected)
        if had_events and not failed:
            next_transcripts = 0.0
        if collecting is not None and collecting.done():
            try:
                collecting.result()
            except Exception as exc:
                # This runs unattended; log the file/range and retry next pass.
                with Path(str(script) + ".log").open("a") as log:
                    log.write(
                        f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {exc}\n"
                    )
            collecting = None
            next_transcripts = time.monotonic() + 5
        if collecting is None and time.monotonic() >= next_transcripts:
            collecting = collector.submit(collect_transcripts, script, dict(values))
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
    script = Path(sys.argv[1])
    if "--flush-transcripts" in sys.argv:
        values = dict(
            entry.split("=", 1)
            for entry in shlex.split(Path(str(script) + ".env").read_text())
        )
        print(json.dumps(collect_transcripts(script, values, flush=True)))
    else:
        main(script)
