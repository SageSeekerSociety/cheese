"""Durable hook-event spool (append-only log) for the local/tmux backend.

The ``cheese-hook`` forwarder appends every Claude Code hook to a per-topic spool
dir (``CHEESE_HOOK_SPOOL``) BEFORE its best-effort curl, so a 现场 event survives
even when the backend is down during it (a redeploy). The backend reads it to
backfill anything the live path missed — idempotent by event-id.

Two properties this module owns, and why:

**The order is a number, not a clock.** A file is ``<seq>.<event_id>``, where
``seq`` is a zero-padded counter shared by every writer on the spool, so a
lexical sort of the directory IS arrival order. It used to be ``date +%s%N``,
which is three bugs in a trench coat: BSD ``date`` (a device running macOS) has
no ``%N`` and emits a literal ``N``, collapsing a whole second's events into one
sort key ordered by random uuid; a ``date`` that fails outputs nothing, making
the name start with ``.`` — the prefix this module skips, so the event is lost
forever; and a clock stepping backwards reorders history. A counter has none of
those failure modes. Allocation is a claim file (``O_EXCL``, so exactly one
writer can own a number) seeded from a ``.seq`` hint; the hint may be stale or
missing without costing anything but a few extra claim attempts.

**Reading is not consuming.** A reader takes everything after its cursor and
then says how far it got. Deleting on read made the spool a queue with one
owner, which is why the same file was consumed in three incompatible ways (a
turn's reconcile, the startup replay, the delivery-evidence probe) and why the
one that ran first blinded the other two. Retention deletes; readers do not.

Pure file IO (list / parse / append / prune) so it stays docker-free and
unit-testable; the translate + persist + dedup lives in ``ChatService`` (reusing
its existing block helpers).
"""

import contextlib
import json
import os
import time
from collections.abc import Iterable
from pathlib import Path

# Zero-pad width of the sequence prefix. 19 digits is what a nanosecond epoch
# takes, so the counter seeded off a pre-existing spool (whose names ARE ns
# timestamps) keeps sorting after them: the format changes without a cutover
# where new events would suddenly sort before everything already spooled.
SEQ_WIDTH = 19
_SEQ_HINT = ".seq"
_CLAIM_PREFIX = ".n"
_CURSOR = ".cursor"


def _seq_of(name: str) -> int | None:
    """The sequence number a spool filename sorts by, or None if it is not one."""
    head = name.split(".", 1)[0]
    return int(head) if head.isdigit() else None


def next_key(spool: Path) -> str:
    """Claim this spool's next sequence number and return it zero-padded.

    The claim file is the whole of the concurrency control: ``O_EXCL`` means one
    writer wins a number and every other retries the next one, so two hooks
    firing at once get two numbers rather than the same one. The ``.seq`` hint
    only saves the loop from starting at zero — it is written last-wins and may
    lag, which costs a few failed claims and nothing else.
    """
    spool.mkdir(parents=True, exist_ok=True)
    seq = spool / _SEQ_HINT
    try:
        n = int(seq.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        n = _highest(spool)
    while True:
        n += 1
        key = f"{n:0{SEQ_WIDTH}d}"
        try:
            os.close(
                os.open(str(spool / f"{_CLAIM_PREFIX}{key}"), os.O_CREAT | os.O_EXCL)
            )
            break
        except FileExistsError:
            continue
    _write_atomic(seq, str(n))
    return key


def _highest(spool: Path) -> int:
    """The largest sequence already on disk — the seed when the hint is gone.

    Without it a spool whose ``.seq`` was reaped would restart from 1, and every
    new event would sort before the ones already waiting to be read.
    """
    best = 0
    with contextlib.suppress(OSError):
        for entry in spool.iterdir():
            name = entry.name
            if name.startswith(_CLAIM_PREFIX):
                name = name[len(_CLAIM_PREFIX) :]
            value = _seq_of(name)
            if value is not None and value > best:
                best = value
    return best


def _write_atomic(path: Path, text: str) -> None:
    """Replace a file's contents in one step. Readers see the old value or the
    new one — a torn read of the sequence hint would send a writer backwards."""
    tmp = path.parent / f".tmp.{path.name}.{os.getpid()}"
    try:
        tmp.write_text(text, encoding="utf-8")
        # The spool is shared between the sandbox's uid and the backend's, so
        # whoever creates the file must not be the only one able to replace it.
        with contextlib.suppress(OSError):
            os.chmod(tmp, 0o666)
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()


def spool_entries(
    spool: Path, after: str | None = None
) -> list[tuple[Path, str, dict | None]]:
    """The spool's events oldest-first as ``(path, event_id, hook_payload)``.

    ``after`` is a cursor — a filename previously returned by this function —
    and only entries that sort strictly after it come back. Passing None reads
    the whole spool.

    Dot-prefixed names (in-flight temp files, claim files, the cursor and the
    sequence hint) are bookkeeping, never events. A file that can't be parsed as
    a JSON object yields ``payload=None`` so the caller still moves its cursor
    past it — a corrupt entry never blocks the rest.
    """
    if not spool.is_dir():
        return []
    out: list[tuple[Path, str, dict | None]] = []
    for name in sorted(p.name for p in spool.iterdir()):
        if name.startswith("."):
            continue
        if after is not None and name <= after:
            continue
        path = spool / name
        eid = name.split(".", 1)[1] if "." in name else name
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — a corrupt/partial spool file is skipped
            parsed = None
        out.append((path, eid, parsed if isinstance(parsed, dict) else None))
    return out


def read_cursor(spool: Path) -> str | None:
    """How far this spool has been read. None means "nothing yet"."""
    try:
        value = (spool / _CURSOR).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def write_cursor(spool: Path, name: str) -> None:
    """Record that everything up to and including ``name`` has been handed to
    the persistence path. Never moves backwards: a reader that had to stop early
    (a message still missing a flush) must not undo a cursor another pass has
    already advanced past."""
    current = read_cursor(spool)
    if current is not None and name <= current:
        return
    with contextlib.suppress(OSError):
        spool.mkdir(parents=True, exist_ok=True)
        _write_atomic(spool / _CURSOR, name)


def append(spool: Path, eid: str, payload: dict) -> None:
    """Atomically add one hook payload to the spool (same ``<seq>.<eid>`` naming
    the cheese-hook forwarder uses — temp + rename). Server-side twin of the
    forwarder's write: the /sandbox/hooks endpoint parks an event that arrived
    with NO turn listening, so a later read materializes it as history instead
    of it being dropped (or replayed into a live queue)."""
    spool.mkdir(parents=True, exist_ok=True)
    key = next_key(spool)
    tmp = spool / f".tmp.{eid}"
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.rename(spool / f"{key}.{eid}")


def remove(paths: Iterable[Path]) -> None:
    """Delete spool files. A failed unlink is non-fatal — the file is just read
    again and deduped by event-id, so it can't cause loss."""
    for path in paths:
        try:
            path.unlink()
        except OSError:
            pass


def prune(spool: Path, *, older_than_s: float) -> int:
    """Retention: drop read events (and their claim files) that are old enough
    that nothing will ask for them again. Returns how many files went.

    This is the ONLY thing that deletes. Nothing before the cursor is needed by
    a reader, but a spool is also the record a person reads when asking what
    that session did, so it goes on age rather than the instant it is read.
    """
    cursor = read_cursor(spool)
    if cursor is None or not spool.is_dir():
        return 0
    deadline = time.time() - older_than_s
    dropped = 0
    for entry in sorted(spool.iterdir(), key=lambda p: p.name):
        name = entry.name
        claim = name.startswith(_CLAIM_PREFIX)
        sortable = name[len(_CLAIM_PREFIX) :] if claim else name
        if not claim and name.startswith("."):
            continue
        if _seq_of(sortable) is None or sortable > cursor:
            continue
        try:
            if entry.stat().st_mtime > deadline:
                continue
            entry.unlink()
        except OSError:
            continue
        dropped += 1
    return dropped
