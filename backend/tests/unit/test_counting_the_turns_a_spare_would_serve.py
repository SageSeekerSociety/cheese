"""`scripts/dev/count-fresh-cold-launches.py` counts each cold turn once, by the
launch that turn started, and times only what that launch measured.

Each case is the journal (`journalctl -o short-iso-precise`) of a few turns, in
the shape the backend writes it: the event's own fields, then the logger's
name and the context the turn bound (``topic=<8 chars> turn=...``).
"""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parents[3] / "scripts" / "dev" / "count-fresh-cold-launches.py"
_spec = importlib.util.spec_from_file_location("count_fresh_cold_launches", SCRIPT)
assert _spec is not None and _spec.loader is not None
counter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(counter)

DAY = "2026-11-28"
TOPIC = "aabbccdd-4242-4000-8000-000000000001"
SEAT = "reviewer"
ACTING = "cheese-111111112222"
# What a turn binds to every line it logs (`turn/steps/execute.py`).
TURN = "topic=aabbccdd turn=aaaaaaaa"


def _line(at: str, message: str, logger: str, *, turn: bool = False) -> str:
    """One journal line: journald's stamp, then the backend's own line, whose
    stamp is only to the second."""
    second = at.split(".")[0]
    tail = f" {TURN}" if turn else ""
    return (
        f"{DAY}T{at}+0000 dev cheese-backend-1[42]: {DAY} {second} [info     ] "
        f"{message} [{logger}]{tail}\n"
    )


def send(at: str, elapsed_ms: float, launch: str | None, **kw) -> str:
    cold = f"cold=True launch={launch}" if launch else "cold=True"
    return _line(
        at,
        f"room_send_timing topic={TOPIC} seat={SEAT} work=w {cold} outcome=ok "
        f"elapsed_ms={elapsed_ms:.1f} phases_ms={{}}",
        "app.domain.agent.room.sessions",
        **kw,
    )


def greeted(at: str, launch: str, start: str, **kw) -> str:
    return _line(
        at,
        f"session_launch_timing topic={TOPIC} seat={SEAT} launch={launch} "
        f"stage=greeted start={start} phases_ms={{'started': 0.0, 'greeted': 100.0}}",
        "app.domain.agent.session_host.claude_code",
        **kw,
    )


def ready(
    at: str, launch: str, start: str, phases: dict, seat: str = ACTING, **kw
) -> str:
    # The ready line names the seat by the handle the teammate acts under,
    # unless a case says otherwise.
    return _line(
        at,
        f"session_launch_timing topic={TOPIC} seat={seat} launch={launch} "
        f"stage=ready start={start} phases_ms={phases}",
        "app.domain.agent.harness.driven.reporting",
        **kw,
    )


def prewarm(at: str) -> str:
    return _line(
        at,
        f"session_prewarm topic={TOPIC} seat={SEAT} outcome=started elapsed_ms=0",
        "app.domain.agent.prewarm",
    )


def _day(*lines: str) -> dict:
    return counter.count(lines)[DAY]


def test_a_prewarm_on_the_turns_conversation_is_seen_past_the_turns_context():
    # The turn's lines end with the 8-character topic it bound; the prewarm
    # before it names the full one. 20 s apart, it covered that turn.
    day = _day(
        prewarm("12:00:00.000000"),
        greeted("12:00:23.000000", "L1", "fresh", turn=True),
        send("12:00:25.000000", 5000.0, "L1", turn=True),
    )

    assert day["fresh"] == 1
    assert day["spare_helps"] == 0


def test_a_launch_is_timed_by_its_own_ready_line_whatever_handle_that_names():
    day = _day(
        greeted("12:00:23.000000", "L1", "fresh"),
        ready(
            "12:00:24.000000", "L1", "fresh", {"started": 0.0, "claude_init": 7200.0}
        ),
        send("12:00:25.000000", 5000.0, "L1"),
    )

    assert day["spare_helps"] == 1
    assert day["saving_ms"] == [7200.0]


def test_each_turn_is_counted_by_the_launch_it_started():
    # A fresh launch greeted and its turn done within one second, then a
    # resumed launch for the next turn five seconds later.
    day = _day(
        greeted("12:00:10.100000", "L1", "fresh"),
        send("12:00:10.900000", 4000.0, "L1"),
        greeted("12:00:15.000000", "L2", "resume"),
        send("12:00:16.000000", 5250.0, "L2"),
    )

    assert (day["cold"], day["fresh"], day["resume"]) == (2, 1, 1)


def test_a_launch_that_never_got_ready_is_not_timed_by_the_next_one():
    day = _day(
        greeted("12:00:23.000000", "L1", "fresh"),
        send("12:00:25.000000", 5000.0, "L1"),
        greeted("12:00:32.000000", "L2", "resume"),
        ready(
            "12:00:33.000000",
            "L2",
            "resume",
            {"started": 0.0, "claude_init": 9500.0},
            seat=SEAT,
        ),
        send("12:00:34.000000", 6000.0, "L2"),
    )

    assert day["spare_helps"] == 1
    assert day["saving_ms"] == []
    assert day["untimed"] == 1


def test_a_prewarm_moments_before_a_turn_is_seen_inside_the_same_second():
    # Prewarm at .200, turn from .400 to 11.900: the backend's own stamps
    # (10 and 11) would put the turn's start at 09.5, before the prewarm.
    day = _day(
        prewarm("12:00:10.200000"),
        greeted("12:00:11.000000", "L1", "fresh"),
        send("12:00:11.900000", 1500.0, "L1"),
    )

    assert day["spare_helps"] == 0


def test_a_launch_without_a_started_mark_stays_out_of_the_median():
    # A shell without EPOCHREALTIME: the runner's earliest mark is runner_up.
    day = _day(
        greeted("12:00:23.000000", "L1", "fresh"),
        ready(
            "12:00:24.000000",
            "L1",
            "fresh",
            {"runner_up": 0.0, "claude_init": 1300.0},
            seat=SEAT,
        ),
        send("12:00:25.000000", 5000.0, "L1"),
    )

    assert day["spare_helps"] == 1
    assert day["saving_ms"] == []
    assert day["untimed"] == 1


def test_a_turn_whose_line_names_no_launch_is_unmatched():
    day = _day(
        greeted("12:00:23.000000", "L1", "fresh"),
        send("12:00:25.000000", 5000.0, None),
    )

    assert (day["cold"], day["unmatched"], day["fresh"]) == (1, 1, 0)
