"""Durable hook-event spool (write-ahead log) for the local/tmux backend.

The ``cheese-hook`` forwarder appends every Claude Code hook to a per-topic spool
dir (``CHEESE_HOOK_SPOOL``) BEFORE its best-effort curl, so a 现场 event survives
even when the backend is down during it (a redeploy). ``ChatService._reconcile_spool``
drains this at each turn start: it materializes any tool-event (施工现场) hook not
already persisted — idempotent by event-id — then removes the spooled files. The
live curl path is unchanged; the spool only backfills the gap.

This module is pure file IO (list / parse / remove) so it stays docker-free and
unit-testable; the translate + persist + dedup lives in ``ChatService`` (reusing
its existing block helpers).
"""

import json
from collections.abc import Iterable
from pathlib import Path


def spool_entries(spool: Path) -> list[tuple[Path, str, dict | None]]:
    """Return the spool's events oldest-first as ``(path, event_id, hook_payload)``.

    Filenames are ``<ns_timestamp>.<event_id>`` (written atomically via temp+rename),
    so a lexical sort of the fixed-width ns prefix is chronological. In-flight temp
    files (dot-prefixed ``.tmp.*``) are skipped. A file that can't be parsed as a
    JSON object yields ``payload=None`` so the caller still removes it (self-cleaning)
    without materializing anything — a corrupt entry never blocks the rest."""
    if not spool.is_dir():
        return []
    out: list[tuple[Path, str, dict | None]] = []
    for name in sorted(p.name for p in spool.iterdir()):
        if name.startswith("."):
            continue
        path = spool / name
        eid = name.split(".", 1)[1] if "." in name else name
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — a corrupt/partial spool file is skipped
            parsed = None
        out.append((path, eid, parsed if isinstance(parsed, dict) else None))
    return out


def append(spool: Path, eid: str, payload: dict) -> None:
    """Atomically add one hook payload to the spool (same ``<ns>.<eid>`` naming
    the cheese-hook forwarder uses — temp + rename). Server-side twin of the
    forwarder's write: the /sandbox/hooks endpoint parks an event that arrived
    with NO turn listening, so the next turn's reconcile materializes it as
    history instead of it being dropped (or replayed into a live queue)."""
    import time

    spool.mkdir(parents=True, exist_ok=True)
    tmp = spool / f".tmp.{eid}"
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.rename(spool / f"{time.time_ns()}.{eid}")


def remove(paths: Iterable[Path]) -> None:
    """Delete reconciled spool files. A failed unlink is non-fatal — the file is
    just re-read next reconcile and deduped by event-id, so it can't cause loss."""
    for path in paths:
        try:
            path.unlink()
        except OSError:
            pass
