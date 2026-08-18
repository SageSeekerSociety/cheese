"""事件流是一条只追加的日志——顺序由号决定，读到哪由游标决定。

Two properties, both of which used to fail in ways nobody could see:

**Order.** The name a hook is spooled under used to be ``date +%s%N``. On a BSD
userland — a device running macOS — ``%N`` is not a format, so every event in
one second shares a sort key and orders by random uuid; when ``date`` fails
outright the name starts with ``.`` and every reader skips it forever. The name
is a counter now, and these tests hold it to what a counter is for: distinct,
increasing, and agreed on by every writer on the spool.

**Position.** Reading used to delete, so whichever of the spool's three readers
ran first blinded the other two. A cursor separates "I have read this" from "this
may be thrown away", and nothing but retention deletes.
"""

import json
import os
import re
import subprocess
import time
from pathlib import Path

from app.domain.agent import event_spool
from app.domain.agent.hooks_substrate import CHEESE_HOOK_SCRIPT


def _hook(tmp_path: Path) -> Path:
    """The real forwarder, extracted to a runnable file."""
    script = tmp_path / "cheese-hook"
    script.write_text(CHEESE_HOOK_SCRIPT, encoding="utf-8")
    script.chmod(0o755)
    return script


def _fire(script: Path, spool: Path, payload: dict) -> None:
    subprocess.run(
        ["/bin/sh", str(script)],
        input=json.dumps(payload).encode(),
        env={**os.environ, "CHEESE_HOOK_SPOOL": str(spool), "CHEESE_HOOK_URL": ""},
        check=True,
        capture_output=True,
    )


def _names(spool: Path) -> list[str]:
    return sorted(p.name for p in spool.iterdir() if not p.name.startswith("."))


def _eids(spool: Path, *, unread: bool = False) -> list[str]:
    after = event_spool.read_cursor(spool) if unread else None
    return [eid for _p, eid, _payload in event_spool.spool_entries(spool, after=after)]


def test_the_forwarder_and_the_backend_share_one_sequence(tmp_path):
    """Both writers append to the SAME directory — the sandbox's forwarder on
    every hook, the backend when it parks an event no turn was listening for.
    Two counters would interleave two orderings, so there is one."""
    spool = tmp_path / "spool"
    script = _hook(tmp_path)
    _fire(script, spool, {"hook_event_name": "SessionStart"})
    event_spool.append(spool, "parked", {"hook_event_name": "Stop"})
    _fire(script, spool, {"hook_event_name": "MessageDisplay"})

    assert _eids(spool)[1] == "parked"
    seqs = [int(name.split(".", 1)[0]) for name in _names(spool)]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == 3


def test_concurrent_hooks_never_share_a_number(tmp_path):
    """Parallel tool calls fire parallel hooks. Two events under one number
    would make the cursor ambiguous — reading past one would skip the other."""
    spool = tmp_path / "spool"
    script = _hook(tmp_path)
    procs = [
        subprocess.Popen(
            ["/bin/sh", str(script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "CHEESE_HOOK_SPOOL": str(spool), "CHEESE_HOOK_URL": ""},
        )
        for _ in range(24)
    ]
    for proc in procs:
        proc.communicate(b'{"hook_event_name":"PreToolUse"}')
    seqs = [name.split(".", 1)[0] for name in _names(spool)]
    assert len(seqs) == 24
    assert len(set(seqs)) == 24


def test_a_reaped_sequence_hint_does_not_rewind_the_order(tmp_path):
    """The hint is a cache. Losing it (retention, a wiped directory) must not
    send the next event to the FRONT of everything still waiting to be read."""
    spool = tmp_path / "spool"
    script = _hook(tmp_path)
    _fire(script, spool, {"hook_event_name": "SessionStart"})
    event_spool.append(spool, "second", {"hook_event_name": "MessageDisplay"})
    before = list(_names(spool))

    (spool / ".seq").unlink()
    _fire(script, spool, {"hook_event_name": "Stop"})
    event_spool.append(spool, "fourth", {"hook_event_name": "Stop"})

    after = _names(spool)
    assert after[:2] == before  # nothing landed ahead of what was already there
    assert after == sorted(after)
    assert after[-1].endswith(".fourth")


def test_a_spool_already_named_by_the_clock_keeps_its_order(tmp_path):
    """A backend deploying onto a spool the old forwarder wrote must not put its
    first new event before events already waiting there."""
    spool = tmp_path / "spool"
    spool.mkdir()
    (spool / f"{time.time_ns()}.legacy").write_text(
        '{"hook_event_name":"PreToolUse"}', encoding="utf-8"
    )
    event_spool.append(spool, "new", {"hook_event_name": "Stop"})
    assert _eids(spool) == ["legacy", "new"]


def test_the_name_is_never_a_dot(tmp_path):
    """The old naming's worst failure: a `date` that produced nothing made the
    file `.<eid>`, which every reader skips as bookkeeping — the event was on
    disk and invisible forever. The counter cannot produce an empty name."""
    spool = tmp_path / "spool"
    _fire(_hook(tmp_path), spool, {"hook_event_name": "Stop"})
    assert re.fullmatch(r"\d{19}\..+", _names(spool)[0])


def test_reading_leaves_the_events_for_the_next_reader(tmp_path):
    """Three readers share this spool (a turn's reconcile, the crash replay, the
    delivery probe). Consuming on read is what let the first one blind the rest."""
    spool = tmp_path / "spool"
    event_spool.append(spool, "a", {"hook_event_name": "PreToolUse"})
    event_spool.append(spool, "b", {"hook_event_name": "Stop"})

    first = event_spool.spool_entries(spool, after=event_spool.read_cursor(spool))
    assert [eid for _p, eid, _payload in first] == ["a", "b"]
    assert _eids(spool) == ["a", "b"]  # still all there for whoever reads next

    event_spool.write_cursor(spool, first[0][0].name)
    assert _eids(spool, unread=True) == ["b"]


def test_the_cursor_only_moves_forward(tmp_path):
    """A reader that had to stop early — a message still missing a flush — must
    not undo the position another pass already reached."""
    spool = tmp_path / "spool"
    event_spool.append(spool, "a", {"hook_event_name": "PreToolUse"})
    event_spool.append(spool, "b", {"hook_event_name": "Stop"})
    names = _names(spool)
    event_spool.write_cursor(spool, names[1])
    event_spool.write_cursor(spool, names[0])
    assert event_spool.read_cursor(spool) == names[1]


def test_retention_drops_only_what_has_been_read(tmp_path):
    """Retention is the only thing that deletes, and it may never outrun the
    cursor — an event dropped before it was read is an event nobody will see."""
    spool = tmp_path / "spool"
    event_spool.append(spool, "old", {"hook_event_name": "PreToolUse"})
    event_spool.append(spool, "unread", {"hook_event_name": "Stop"})
    event_spool.write_cursor(spool, _names(spool)[0])
    then = time.time() - 3600
    for entry in spool.iterdir():
        os.utime(entry, (then, then))

    assert event_spool.prune(spool, older_than_s=7200) == 0  # not old enough
    assert event_spool.prune(spool, older_than_s=60) == 2  # the event + its claim
    assert _eids(spool) == ["unread"]
    # And the number is still not reusable: the next event sorts after the one
    # retention just removed.
    event_spool.append(spool, "next", {"hook_event_name": "Stop"})
    assert _names(spool)[-1].endswith(".next")
