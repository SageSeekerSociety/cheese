"""Count the turns a spare Claude Code would have shortened. Read-only.

A spare is a Claude Code started ahead of the message with `--await-claim` and
claimed when it comes; it can only begin a conversation, never take one up. It
helps a turn that:

- started the session's process itself (`room_send_timing ... cold=True`);
- began a conversation (`session_launch_timing ... start=fresh`);
- had no prewarm start that seat's session in the 60 s before it
  (`session_prewarm ... outcome=started`).

Feed it the backend's log on stdin and it prints, per day, how many cold turns
there were, how many of them were of that kind and their share, and the median
of what a spare would save on each (`started` to `claude_init` in the launch's
`phases_ms`). Turns whose launch logged no `start=` (a runner from before it
said so) are counted apart, never as fresh or resumed.

    ssh <dev box> 'sudo journalctl -t cheese-backend-1 -t cheese-backend-b-1 \
        --since "7 days ago" -o cat' | python3 scripts/dev/count-fresh-cold-launches.py

The log's own timestamps are to the second, so a launch is matched to the turn
whose span holds its greeting, with a second of slack each side.
"""

import ast
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta

PREWARM_WINDOW = timedelta(seconds=60)
SLACK = timedelta(seconds=1)
#: How long after its greeting a launch's ``stage=ready`` line may come.
READY_WITHIN = timedelta(minutes=10)

STAMP = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
EVENT = re.compile(r"\b(room_send_timing|session_launch_timing|session_prewarm) ")
FIELD = re.compile(r"\b(topic|seat|cold|outcome|stage|start|elapsed_ms)=(\S+)")
PHASES = re.compile(r"phases_ms=(\{[^}]*\})")


def parse(lines):
    """(event, time, fields, phases) for each line this reads."""
    for line in lines:
        event, stamp = EVENT.search(line), STAMP.search(line)
        if not event or not stamp:
            continue
        fields = dict(FIELD.findall(line[event.end() :]))
        phases = PHASES.search(line)
        try:
            timing = ast.literal_eval(phases.group(1)) if phases else {}
        except (ValueError, SyntaxError):
            timing = {}
        at = datetime.strptime(stamp.group(1), "%Y-%m-%d %H:%M:%S")
        yield event.group(1), at, fields, timing


def count(lines) -> dict[str, dict]:
    turns, launches, prewarms = [], defaultdict(list), defaultdict(list)
    for event, at, fields, timing in parse(lines):
        seat = (fields.get("topic"), fields.get("seat"))
        if event == "room_send_timing" and fields.get("cold") == "True":
            began = at - timedelta(milliseconds=float(fields.get("elapsed_ms", 0)))
            turns.append((seat, began, at))
        elif event == "session_launch_timing":
            launches[seat].append(
                (at, fields.get("stage"), fields.get("start"), timing)
            )
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
            "saving_ms": [],
        }
    )
    for seat, began, ended in turns:
        day = days[began.date().isoformat()]
        day["cold"] += 1
        greeted = next(
            (
                launch
                for launch in launches[seat]
                if launch[1] == "greeted"
                and began - SLACK <= launch[0] <= ended + SLACK
            ),
            None,
        )
        if greeted is None:
            day["unmatched"] += 1
            continue
        start = greeted[2] if greeted[2] in ("fresh", "resume") else "unknown"
        day[start] += 1
        if start != "fresh":
            continue
        if any(began - PREWARM_WINDOW <= at <= began for at in prewarms[seat]):
            continue
        day["spare_helps"] += 1
        ready = next(
            (
                launch
                for launch in launches[seat]
                if launch[1] == "ready"
                and greeted[0] <= launch[0] <= greeted[0] + READY_WITHIN
            ),
            None,
        )
        timing = ready[3] if ready else {}
        if "claude_init" in timing:
            day["saving_ms"].append(timing["claude_init"] - timing.get("started", 0.0))
    return days


def main():
    days = count(sys.stdin)
    print(
        "day         cold  fresh  resume  unknown  unmatched  spare_helps  share   median_started_to_claude_init_ms"
    )
    for name in sorted(days):
        day = days[name]
        share = day["spare_helps"] / day["cold"] if day["cold"] else 0.0
        saving = statistics.median(day["saving_ms"]) if day["saving_ms"] else None
        print(
            f"{name}  {day['cold']:4}  {day['fresh']:5}  {day['resume']:6}  {day['unknown']:7}"
            f"  {day['unmatched']:9}  {day['spare_helps']:11}  {share:5.1%}"
            f"   {'-' if saving is None else f'{saving:.0f}'}"
        )


if __name__ == "__main__":
    main()
