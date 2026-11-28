"""Count the turns a spare Claude Code would have shortened. Read-only.

A spare is a Claude Code started ahead of the message with `--await-claim` and
claimed when it comes; it can only begin a conversation, never take one up. It
helps a turn that:

- started the session's process itself (`room_send_timing ... cold=True`);
- began a conversation (`session_launch_timing ... start=fresh`);
- had no prewarm start that seat's session in the 60 s before it
  (`session_prewarm ... outcome=started`).

Feed it the backend's journal on stdin and it prints, per day, how many cold
turns there were, how many of them were of that kind and their share, and the
median of what a spare would save on each (`started` to `claude_init` in the
launch's `phases_ms`). Turns whose launch logged no `start=` (a runner from
before it said so) are counted apart, never as fresh or resumed. A turn the
spare helps whose launch did not time both `started` and `claude_init` (no
ready line, or a shell without `EPOCHREALTIME` to mark `started`) is counted in
`untimed` and left out of the median.

    ssh <dev box> 'sudo journalctl -t cheese-backend-1 -t cheese-backend-b-1 \
        --since "7 days ago" -o short-iso-precise' \
        | python3 scripts/dev/count-fresh-cold-launches.py

A turn and its launch are joined by the launch's name (`launch=`), which the
cold turn's line and both of the launch's lines carry; a turn without one (a
backend from before they did) is `unmatched`. Whether the launch began a
conversation is read from its greeted line, or from its ready line when no
greeted line was logged (the greeting's reply was lost and retried). A prewarm is joined to a turn by
conversation and seat, and placed in time by the journal's own microsecond
timestamp (`-o short-iso-precise`): the backend's log line stamps only the
second, too coarse to tell a prewarm from a turn that began just after it.
"""

import ast
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta

PREWARM_WINDOW = timedelta(seconds=60)

#: journalctl's ``short-iso-precise`` stamp, which leads each line.
STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+[+-]\d{2}:?\d{2})\s")
EVENT = re.compile(r"\b(room_send_timing|session_launch_timing|session_prewarm) ")
FIELD = re.compile(r"\b(topic|seat|launch|cold|outcome|stage|start|elapsed_ms)=(\S+)")
PHASES = re.compile(r"phases_ms=(\{[^}]*\})")


def parse(lines):
    """(event, time, fields, phases) for each line this reads."""
    for line in lines:
        event = EVENT.search(line)
        if not event:
            continue
        stamp = STAMP.search(line)
        if not stamp:
            sys.exit(
                "count-fresh-cold-launches: a line has no microsecond journal "
                "stamp; read the journal with `-o short-iso-precise`:\n" + line
            )
        # The event's own fields come first; the formatter appends the
        # context bound to the turn after them (``topic=<8 chars> turn=...``),
        # which must not replace them.
        fields: dict[str, str] = {}
        for name, value in FIELD.findall(line[event.end() :]):
            fields.setdefault(name, value)
        phases = PHASES.search(line)
        try:
            timing = ast.literal_eval(phases.group(1)) if phases else {}
        except (ValueError, SyntaxError):
            timing = {}
        at = datetime.strptime(stamp.group(1), "%Y-%m-%dT%H:%M:%S.%f%z")
        yield event.group(1), at, fields, timing


def count(lines) -> dict[str, dict]:
    turns, prewarms = [], defaultdict(list)
    starts: dict[str, str | None] = {}
    readies: dict[str, dict] = {}
    for event, at, fields, timing in parse(lines):
        seat = (fields.get("topic"), fields.get("seat"))
        launch = fields.get("launch")
        if launch == "None":
            launch = None
        if event == "room_send_timing" and fields.get("cold") == "True":
            began = at - timedelta(milliseconds=float(fields.get("elapsed_ms", 0)))
            turns.append((seat, launch, began))
        elif event == "session_launch_timing" and launch is not None:
            if fields.get("stage") == "greeted":
                starts[launch] = fields.get("start")
            elif fields.get("stage") == "ready":
                readies[launch] = timing
                # The greeting's own line is missing when its reply was lost
                # and the ping retried; the ready line says the same.
                starts.setdefault(launch, fields.get("start"))
        elif event == "session_prewarm" and fields.get("outcome") == "started":
            prewarms[seat].append(at)

    days: dict[str, dict] = defaultdict(
        lambda: {
            "cold": 0,
            "fresh": 0,
            "resume": 0,
            "unknown": 0,
            "unmatched": 0,
            "spare_helps": 0,
            "untimed": 0,
            "saving_ms": [],
        }
    )
    for seat, launch, began in turns:
        day = days[began.date().isoformat()]
        day["cold"] += 1
        if launch not in starts:
            day["unmatched"] += 1
            continue
        start = starts[launch] if starts[launch] in ("fresh", "resume") else "unknown"
        day[start] += 1
        if start != "fresh":
            continue
        if any(began - PREWARM_WINDOW <= at <= began for at in prewarms[seat]):
            continue
        day["spare_helps"] += 1
        timing = readies.get(launch, {})
        if "started" in timing and "claude_init" in timing:
            day["saving_ms"].append(timing["claude_init"] - timing["started"])
        else:
            day["untimed"] += 1
    return days


def main():
    days = count(sys.stdin)
    print(
        "day         cold  fresh  resume  unknown  unmatched  spare_helps  share   untimed"
        "  median_started_to_claude_init_ms"
    )
    for name in sorted(days):
        day = days[name]
        share = day["spare_helps"] / day["cold"] if day["cold"] else 0.0
        saving = statistics.median(day["saving_ms"]) if day["saving_ms"] else None
        print(
            f"{name}  {day['cold']:4}  {day['fresh']:5}  {day['resume']:6}  {day['unknown']:7}"
            f"  {day['unmatched']:9}  {day['spare_helps']:11}  {share:5.1%}"
            f"  {day['untimed']:7}  {'-' if saving is None else f'{saving:.0f}'}"
        )


if __name__ == "__main__":
    main()
