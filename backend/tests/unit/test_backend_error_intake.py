"""Backend-error intake guard rails.

The issue this implements says the feature succeeds or fails on GRANULARITY, not
on whether the channel works — 「宁可漏报也不能刷屏：房间是对话，不是监控大盘。」
So the flood assertion below is the acceptance criterion in executable form.
"""

import uuid

from app.domain.backend_log import (
    DEDUP_WINDOW_S,
    MAX_BLOCKS_PER_HOUR,
    BackendErrorIn,
    BackendErrorIntake,
    event_content,
    event_meta,
    fingerprint,
    from_exception,
    room_from_path,
    summary_content,
)

STACK = 'Traceback:\n  File "app/api/routes/x.py", line 12, in go\nValueError: nope'


def _err(**kw) -> BackendErrorIn:
    return BackendErrorIn(**{"message": "boom", "exc_type": "ValueError", **kw})


# --- 「宁可漏报也不能刷屏」 ------------------------------------------------


def test_flood_of_one_error_produces_at_most_a_handful_of_blocks() -> None:
    """800 occurrences of the SAME error in one window → one block, not 800."""
    intake = BackendErrorIntake()
    fp = fingerprint(_err(stack=STACK))
    emitted = [
        v for i in range(800) if (v := intake.admit("p", fp, now=1000.0 + i * 0.1))
    ]
    assert len(emitted) == 1
    assert emitted[0].kind == "detail"


def test_sustained_flood_stays_bounded_over_a_whole_hour() -> None:
    """A route failing ~every 50ms for an hour (≈72k failures) must not be able
    to outrun the per-project hourly cap."""
    intake = BackendErrorIntake()
    fp = fingerprint(_err(stack=STACK))
    emitted = [
        v for i in range(72_000) if (v := intake.admit("p", fp, now=1000.0 + i * 0.05))
    ]
    assert len(emitted) <= MAX_BLOCKS_PER_HOUR


def test_burst_collapses_into_one_summary_carrying_the_real_count() -> None:
    intake = BackendErrorIntake()
    first = intake.admit("p", "fp", now=1000.0)
    assert first.kind == "detail"
    for i in range(1, 800):
        assert not intake.admit("p", "fp", now=1000.0 + i * 0.1)
    # The window closes when the next occurrence arrives past it.
    later = intake.admit("p", "fp", now=1000.0 + DEDUP_WINDOW_S + 1)
    assert later.kind == "summary"
    assert later.count == 800


def test_a_burst_that_stopped_still_reports_its_size() -> None:
    """The case the recurrence-driven summary could never cover: 500 failures in
    four minutes, then someone fixes it and it never fires again. The count is
    the severity, so it must not die with the window."""
    intake = BackendErrorIntake()
    room = {"sample": _err(), "topic_id": uuid.uuid4(), "project_uuid": uuid.uuid4()}
    intake.admit("p", "fp", now=1000.0, **room)
    for i in range(1, 500):
        intake.admit("p", "fp", now=1000.0 + i * 0.4, **room)

    assert intake.sweep(now=1000.0 + 100) == []  # window still open — say nothing

    bursts = intake.sweep(now=1000.0 + DEDUP_WINDOW_S + 1)
    assert len(bursts) == 1
    assert bursts[0].count == 500
    assert bursts[0].topic_id == room["topic_id"]


def test_sweeping_twice_does_not_report_the_burst_twice() -> None:
    intake = BackendErrorIntake()
    room = {"sample": _err(), "topic_id": uuid.uuid4(), "project_uuid": uuid.uuid4()}
    intake.admit("p", "fp", now=1000.0, **room)
    intake.admit("p", "fp", now=1000.1, **room)

    assert len(intake.sweep(now=1000.0 + DEDUP_WINDOW_S + 1)) == 1
    assert intake.sweep(now=1000.0 + DEDUP_WINDOW_S + 2) == []


def test_a_single_occurrence_gets_no_summary_when_its_window_closes() -> None:
    """It was already reported in full — 「5 分钟内 1 次」 is noise."""
    intake = BackendErrorIntake()
    intake.admit(
        "p",
        "fp",
        now=1000.0,
        sample=_err(),
        topic_id=uuid.uuid4(),
        project_uuid=uuid.uuid4(),
    )
    assert intake.sweep(now=1000.0 + DEDUP_WINDOW_S + 1) == []


def test_a_swept_burst_still_costs_an_hourly_slot() -> None:
    """The cap outranks the summary: closing windows must not become a way to
    put more blocks in the room than the ceiling allows."""
    intake = BackendErrorIntake()
    room = {"sample": _err(), "topic_id": uuid.uuid4(), "project_uuid": uuid.uuid4()}
    for i in range(MAX_BLOCKS_PER_HOUR):
        intake.admit("p", f"fp{i}", now=1000.0, **room)
        intake.admit("p", f"fp{i}", now=1000.1, **room)  # make each one a burst

    assert intake.sweep(now=1000.0 + DEDUP_WINDOW_S + 1) == []


def test_lone_recurrence_after_the_window_reports_in_full_again() -> None:
    """One error, then quiet, then the same error again: that is not a burst —
    it gets a normal detail line, not a "1 次" summary."""
    intake = BackendErrorIntake()
    assert intake.admit("p", "fp", now=1000.0).kind == "detail"
    assert intake.admit("p", "fp", now=1000.0 + DEDUP_WINDOW_S + 1).kind == "detail"


def test_hourly_cap_is_per_project() -> None:
    intake = BackendErrorIntake()
    for i in range(MAX_BLOCKS_PER_HOUR):
        assert intake.admit("p", f"fp{i}", now=1000.0 + i)
    assert not intake.admit("p", "fresh", now=1000.0 + MAX_BLOCKS_PER_HOUR)
    # A noisy project must not silence a quiet one.
    assert intake.admit("q", "fresh", now=1000.0 + MAX_BLOCKS_PER_HOUR)


def test_cap_lifts_once_the_hour_rolls_off() -> None:
    intake = BackendErrorIntake()
    for i in range(MAX_BLOCKS_PER_HOUR):
        assert intake.admit("p", f"fp{i}", now=1000.0 + i)
    assert not intake.admit("p", "fresh", now=2000.0)
    assert intake.admit("p", "fresh2", now=1000.0 + 3601)


# --- fingerprint identity -------------------------------------------------


def test_same_bug_through_different_ids_is_one_fingerprint() -> None:
    """The single most important normalization: a broken /api/topics/{uuid}
    route sees a different uuid from every caller. Without this, dedup never
    fires and the cap is the only thing standing between a bug and the room."""
    a = _err(where=f"POST /api/topics/{uuid.uuid4()}/chat", stack=STACK)
    b = _err(where=f"POST /api/topics/{uuid.uuid4()}/chat", stack=STACK)
    assert fingerprint(a) == fingerprint(b)


def test_row_ids_in_the_message_do_not_split_a_fingerprint() -> None:
    a = _err(message="user 918273 not found", stack=STACK)
    b = _err(message="user 445566 not found", stack=STACK)
    assert fingerprint(a) == fingerprint(b)


def test_different_exception_type_or_throw_site_is_a_different_error() -> None:
    base = _err(stack=STACK)
    other_type = _err(exc_type="KeyError", stack=STACK)
    other_site = _err(
        stack='Traceback:\n  File "app/api/routes/y.py", line 99, in go\n'
        "ValueError: nope"
    )
    assert fingerprint(base) != fingerprint(other_type)
    assert fingerprint(base) != fingerprint(other_site)


def test_fingerprint_keys_on_the_deepest_frame_not_the_middleware() -> None:
    """Shallow frames are identical for every error in the process; keying on
    them would merge unrelated bugs into one."""
    shared_top = 'Traceback:\n  File "app/main.py", line 3, in mw\n'
    a = _err(stack=shared_top + '  File "a.py", line 1, in go\nValueError: x')
    b = _err(stack=shared_top + '  File "b.py", line 1, in go\nValueError: x')
    assert fingerprint(a) != fingerprint(b)


# --- rendering: one line for a human, everything for 芝士 -------------------


def test_detail_renders_one_line_and_parks_the_stack_in_meta() -> None:
    err = _err(where="POST /api/topics/x/chat", stack=STACK, request_id="abc123")
    content = event_content(err)
    assert "\n" not in content
    assert "ValueError" in content and "boom" in content
    # 这是不是一条报错，读码，不读开头那个字符。
    meta = event_meta(err, BackendErrorIntake().admit("p", "fp", now=1.0))
    assert meta["event_type"] == "backend_error"
    assert meta["stack"] == STACK
    assert meta["request_id"] == "abc123"
    assert "summary" not in meta


def test_long_message_is_truncated_in_the_line_but_whole_in_the_stack() -> None:
    err = _err(message="x" * 900, stack=STACK)
    assert len(event_content(err)) < 300
    meta = event_meta(err, BackendErrorIntake().admit("p", "f", now=1.0))
    assert meta["stack"] == STACK


def test_summary_line_states_the_count_and_the_window() -> None:
    err = _err()
    content = summary_content(err, 800)
    assert "800" in content and "5" in content
    meta = event_meta(err, BackendErrorIntake().admit("p", "f", now=1.0))
    assert meta.get("summary") is None  # a detail verdict carries no count


def test_summary_meta_marks_itself_as_an_aggregate() -> None:
    intake = BackendErrorIntake()
    intake.admit("p", "fp", now=1000.0)
    for i in range(1, 50):
        intake.admit("p", "fp", now=1000.0 + i)
    verdict = intake.admit("p", "fp", now=1000.0 + DEDUP_WINDOW_S + 1)
    meta = event_meta(_err(), verdict)
    assert meta["summary"] is True
    assert meta["count"] == 50


# --- building a report from a live exception -------------------------------


def test_from_exception_captures_type_message_and_throw_site() -> None:
    def boom() -> None:
        raise ValueError("kaboom")

    try:
        boom()
    except ValueError as exc:
        err = from_exception(exc, where="POST /x", request_id="rid")

    assert err.exc_type == "ValueError"
    assert err.message == "kaboom"
    assert "boom" in (err.stack or "")
    assert err.where == "POST /x"


def test_from_exception_scrubs_credentials_out_of_the_traceback() -> None:
    """These reports are readable by everyone in the room, so a token that
    happens to sit in an exception message must not ride along."""
    try:
        raise RuntimeError("upstream rejected token=sk-live-abcdef123 for user")
    except RuntimeError as exc:
        err = from_exception(exc)

    assert "sk-live-abcdef123" not in err.message
    assert "sk-live-abcdef123" not in (err.stack or "")
    assert "***" in err.message


# --- which room a failing request belongs to -------------------------------


def test_room_from_path_reads_topic_and_project() -> None:
    tid, pid = uuid.uuid4(), uuid.uuid4()
    assert room_from_path(f"/topics/{tid}/blocks") == (None, tid)
    assert room_from_path(f"/projects/{pid}/memory") == (pid, None)
    assert room_from_path(f"/projects/{pid}/topics/{tid}") == (pid, tid)


def test_room_from_path_ignores_paths_that_name_no_room() -> None:
    assert room_from_path("/users/me") == (None, None)
    assert room_from_path("/projects/by-task/abc") == (None, None)
    assert room_from_path("/health") == (None, None)
