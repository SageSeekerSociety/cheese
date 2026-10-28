"""Backend-error intake guard rails.

The issue this implements says the feature succeeds or fails on GRANULARITY, not
on whether the channel works — 「宁可漏报也不能刷屏：房间是对话，不是监控大盘。」
So the flood assertion below is the acceptance criterion in executable form.
"""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

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


@pytest.mark.parametrize("kind", ["GatewayHTTPError", "GatewayStreamError"])
async def test_one_failed_model_call_is_kept_without_alarming(monkeypatch, kind):
    """Upstream overload fails about one call in 130 on an ordinary day, and the
    caller retries it. Each one is recorded; none of them is news."""
    backend_log, topic, added, alerts = _recorded(monkeypatch)
    error = BackendErrorIn(
        message="Model response stream failed before completion",
        exc_type=kind,
        where="model gateway",
        request_id="call-1",
    )
    for _ in range(2):
        await backend_log.record(
            None, project_id=topic.project_id, topic_id=topic.id, errors=[error]
        )
    assert added.await_count == 1
    assert alerts == []


async def _fail(backend_log, topic, kind, n=1, where="model gateway", **kw):
    for _ in range(n):
        error = BackendErrorIn(
            message="The model was never reached",
            exc_type=kind,
            where=where,
            request_id=f"call-{uuid.uuid4().hex[:8]}",
            **kw,
        )
        await backend_log.record(
            None, project_id=topic.project_id, topic_id=topic.id, errors=[error]
        )


def _bursts(alerts):
    return [a for a in alerts if str(a[1].get("key")).startswith("model-failure-burst")]


async def test_a_burst_of_model_failures_alerts_with_the_counts(monkeypatch):
    """What a person can act on is the model path going down: many failures in
    a few minutes, of whatever kind. That is told with how many of each."""
    backend_log, topic, added, alerts = _recorded(monkeypatch)
    await _fail(backend_log, topic, "GatewayUnreachable", 40)
    await _fail(backend_log, topic, "GatewayHTTPError", 40)
    told = _bursts(alerts)
    assert told
    last = " ".join(told[-1][0][1])
    assert "GatewayUnreachable 40" in last and "GatewayHTTPError 40" in last


async def test_failures_spread_over_an_ordinary_day_do_not_alert(monkeypatch):
    """A failure a minute is a busy upstream, not an outage."""
    backend_log, topic, added, alerts = _recorded(monkeypatch)
    clock = [1_000_000.0]
    monkeypatch.setattr(backend_log.time, "time", lambda: clock[0])
    for _ in range(120):
        await _fail(backend_log, topic, "GatewayHTTPError")
        clock[0] += 60
    assert _bursts(alerts) == []


async def test_an_outage_that_goes_on_keeps_being_reported(monkeypatch):
    """An outage that goes on for half an hour is still handed to the alerting
    module after its first minutes, which decides when it is news again. Told
    only at the moment the threshold was crossed, an outage that began inside
    the repeat window of an earlier spike was never reported at all."""
    backend_log, topic, added, alerts = _recorded(monkeypatch)
    clock = [1_000_000.0]
    monkeypatch.setattr(backend_log.time, "time", lambda: clock[0])
    for _ in range(10 * 60):
        await _fail(backend_log, topic, "GatewayUnreachable")
        clock[0] += 1
    early = len(_bursts(alerts))
    for _ in range(20 * 60):
        await _fail(backend_log, topic, "GatewayUnreachable")
        clock[0] += 1
    assert len(_bursts(alerts)) > early


async def test_only_model_failures_count_toward_the_burst(monkeypatch):
    backend_log, topic, added, alerts = _recorded(monkeypatch)
    await _fail(backend_log, topic, "ValueError", 80)
    await _fail(backend_log, topic, "ClientAbandonedTurn", 80)
    assert _bursts(alerts) == []


@pytest.mark.parametrize(
    ("kind", "category"),
    [
        ("SubscriptionHTTPError", "http_auth"),
        ("GatewayHTTPError", "http_auth"),
        ("SubscriptionEgressOffline", "egress_offline"),
        ("GatewayUpstreamOutOfCredit", "provider_credit"),
    ],
)
async def test_a_failure_someone_can_fix_is_told_at_once(monkeypatch, kind, category):
    """A refused credential, an offline egress or a provider account out of
    credit fails every turn of a quiet deployment, far below any burst
    threshold; it is told the first time."""
    backend_log, topic, added, alerts = _recorded(monkeypatch)
    await _fail(
        backend_log, topic, kind, where="model subscription", upstream_failure=category
    )
    assert len(alerts) == 1
    assert alerts[0][1]["key"] != "model-failure-burst"


async def test_each_empty_provider_account_is_its_own_alert(monkeypatch):
    """Two rows out of credit are two accounts to top up: the second must not
    be held back as a repeat of the first, while the same row named again is
    the same alert (`alerting` tells a key once per repeat window)."""
    backend_log, topic, added, alerts = _recorded(monkeypatch)
    for row in (
        "deepseek-flash deepseek/deepseek-chat 1a2b3c4d",
        "glm-5.3 anthropic/glm-5.3 5e6f7a8b",
    ):
        for _ in range(2):
            error = BackendErrorIn(
                message=f"The provider account behind gateway row {row} is out of credit",
                exc_type="GatewayUpstreamOutOfCredit",
                where=f"model gateway: {row}",
                upstream_failure="provider_credit",
            )
            await backend_log.record(
                None, project_id=topic.project_id, topic_id=topic.id, errors=[error]
            )
    keys = [kw["key"] for _args, kw in alerts]
    assert len(keys) == 4 and len(set(keys)) == 2, keys
    told = [" ".join(args[1]) for args, _kw in alerts]
    assert "deepseek/deepseek-chat" in told[0] and "anthropic/glm-5.3" in told[-1], told


STACK = 'Traceback:\n  File "app/api/routes/x.py", line 12, in go\nValueError: nope'


# --- what the metering proxy observed around a dropped model turn -------------


def test_the_stream_note_is_shown_but_never_fingerprinted() -> None:
    """The count rides beside the message, never inside it. `fingerprint()` reads
    the message, so a number in it would split one recurring failure into a new
    error every time the turn happened to be cut somewhere else."""
    from app.domain.backend_log import _headline

    zero = _err(
        exc_type="GatewayStreamError",
        where="model gateway",
        received_events=0,
        upstream_failure="timeout",
        ended_by="upstream",
    )
    many = _err(
        exc_type="GatewayStreamError",
        where="model gateway",
        received_events=8123,
        upstream_failure="timeout",
        ended_by="upstream",
    )
    assert fingerprint(zero) == fingerprint(many)
    assert "events: 0" in _headline(zero)
    assert "events: 8123" in _headline(many)
    assert "upstream: timeout" in _headline(many)
    assert "ended by: upstream" in _headline(many)


def test_event_meta_carries_what_the_proxy_observed() -> None:
    from app.domain.backend_log import Verdict

    meta = event_meta(
        _err(
            exc_type="SubscriptionStreamError",
            where="model subscription",
            received_events=12,
            upstream_failure="transport",
            ended_by="upstream",
        ),
        Verdict("event"),
    )
    assert meta["received_events"] == 12
    assert meta["upstream_failure"] == "transport"
    assert meta["ended_by"] == "upstream"


def _recorded(monkeypatch):
    """The intake's three side effects captured, for the alert tests."""
    from app.core import alerting
    from app.domain import backend_log

    topic = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    monkeypatch.setattr(
        backend_log, "_where", AsyncMock(return_value=(topic.project_id, topic.id))
    )
    monkeypatch.setattr(backend_log, "intake", BackendErrorIntake())
    monkeypatch.setattr(backend_log, "model_failures", backend_log._ModelFailureRate())
    added = AsyncMock()
    monkeypatch.setattr(backend_log, "keep_record", added)
    alerts = []
    monkeypatch.setattr(alerting, "send", lambda *args, **kw: alerts.append((args, kw)))
    return backend_log, topic, added, alerts


async def test_a_subscription_lane_failure_alerts_like_a_gateway_one(monkeypatch):
    """The lane is not what makes a failure: before #1447 only
    `where == "model gateway"` alerted, so a stalled subscription lane paged
    nobody — and the record itself was missing entirely."""
    backend_log, topic, added, alerts = _recorded(monkeypatch)
    for i in range(80):
        error = BackendErrorIn(
            message="Model response stream was cut before the upstream ended it",
            exc_type="SubscriptionStreamError",
            where="model subscription",
            request_id=f"req-{i}",
            received_events=0,
            upstream_failure="timeout",
            ended_by="upstream",
        )
        await backend_log.record(
            None, project_id=topic.project_id, topic_id=topic.id, errors=[error]
        )
    assert added.await_count == 1
    assert _bursts(alerts)
    lines = " ".join(alerts[0][0][1])
    assert "SubscriptionStreamError" in lines
    assert "events: 0" in lines and "upstream: timeout" in lines


async def test_a_caller_hanging_up_is_recorded_without_alarming(monkeypatch):
    """The blind spot is worth a record — it is the shape that told us nothing
    before — but it is not evidence that the model failed the room, and an hour
    of people pressing Esc would take the alert slot from the failures under it."""
    backend_log, topic, added, alerts = _recorded(monkeypatch)
    error = BackendErrorIn(
        message="The caller disconnected before the answer finished",
        exc_type="ClientAbandonedTurn",
        where="model subscription",
        received_events=3,
        ended_by="client",
    )
    await backend_log.record(
        None, project_id=topic.project_id, topic_id=topic.id, errors=[error]
    )
    assert added.await_count == 1
    assert alerts == []


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


@pytest.fixture
def delivered(monkeypatch):
    """What actually reaches the channel through the real alerting module, its
    repeat rules included, on a clock the test turns."""
    import time as clock_module

    from app.core import alerting
    from app.domain import backend_log

    topic = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    monkeypatch.setattr(
        backend_log, "_where", AsyncMock(return_value=(topic.project_id, topic.id))
    )
    monkeypatch.setattr(backend_log, "intake", BackendErrorIntake())
    monkeypatch.setattr(backend_log, "model_failures", backend_log._ModelFailureRate())
    monkeypatch.setattr(backend_log, "keep_record", AsyncMock())
    monkeypatch.setattr(
        alerting.settings, "feishu_alert_webhook", "https://example/hook"
    )
    monkeypatch.setattr(alerting, "budget", alerting._Budget())
    monkeypatch.setattr(alerting, "_asked", {})
    monkeypatch.setattr(
        alerting, "REPEAT_KEY_PREFIX", f"cheese:alert:test:{uuid.uuid4().hex}:"
    )
    now = [1_000_000.0]
    monkeypatch.setattr(clock_module, "time", lambda: now[0])
    posted: list[tuple[float, str]] = []

    async def capture(text: str) -> None:
        posted.append((now[0], text))

    monkeypatch.setattr(alerting, "_post", capture)

    async def fail(kind: str, n: int, every_s: float) -> None:
        for _ in range(n):
            await _fail(backend_log, topic, kind)
            while alerting._running:
                await asyncio.gather(*list(alerting._running), return_exceptions=True)
            now[0] += every_s

    return SimpleNamespace(fail=fail, posted=posted, now=now)


async def test_a_gateway_outage_right_after_an_overload_spike_is_told(delivered):
    """An ordinary upstream-overload spike, quiet, then our gateway going down
    for two minutes: the outage reaches the channel, not only the spike."""
    await delivered.fail("GatewayStreamError", 35, 8)
    delivered.now[0] += 15 * 60
    outage_starts = delivered.now[0]
    await delivered.fail("GatewayUnreachable", 100, 1.2)

    during = [text for at, text in delivered.posted if at >= outage_starts]
    assert during and "GatewayUnreachable" in during[0], delivered.posted


@pytest.mark.parametrize("category", ["http_error", "rate_limit", "timeout"])
async def test_an_upstream_that_is_busy_is_not_told_at_once(monkeypatch, category):
    """Overload, rate limits and timeouts are the upstream's, retried by the
    caller; one of them is not news, whatever lane it came from."""
    backend_log, topic, added, alerts = _recorded(monkeypatch)
    await _fail(
        backend_log,
        topic,
        "SubscriptionHTTPError",
        where="model subscription",
        upstream_failure=category,
    )
    assert alerts == []


async def test_the_burst_threshold_is_where_it_says(monkeypatch):
    from app.domain.backend_log import MODEL_FAILURE_BURST

    backend_log, topic, added, alerts = _recorded(monkeypatch)
    await _fail(backend_log, topic, "GatewayUnreachable", MODEL_FAILURE_BURST - 1)
    assert _bursts(alerts) == []
    await _fail(backend_log, topic, "GatewayUnreachable")
    assert len(_bursts(alerts)) == 1
