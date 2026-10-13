"""The backend half of 现场 error reporting.

Two ways in, one intake:
- ``POST /api/backend-errors`` — a process that crashed pushes its own failure.
- the ``report_unhandled_to_room`` middleware — this app pushing ITSELF.

Both are kept as run records for the admin page — a line a person reads at a
glance and the whole stack behind it — and never said in the room they
happened in: the people there are not who reads the platform's own errors.
"""

import re
import time
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import APIRouter
from sqlalchemy import select

from app.core.sandbox_auth import mint_scoped_token
from app.domain import backend_log
from app.domain.run_record.models import RunRecord
from app.main import app
from tests.integration.conftest import post_project


@pytest.fixture(autouse=True)
def _fresh_intake(monkeypatch):
    """The intake singleton dedups for the process lifetime — isolate tests."""
    monkeypatch.setattr(backend_log, "intake", backend_log.BackendErrorIntake())


@pytest.fixture
def in_process_db(client, monkeypatch):
    """The middleware opens its OWN session (the request's is already broken), so
    point that factory at the same database this client reads from."""
    monkeypatch.setattr(
        backend_log, "async_session_factory", client.test_request_factory
    )
    return client


@pytest.fixture
def crashing_route(client):
    """A route that raises for real, so the middleware is exercised through the
    actual stack rather than by calling it directly."""
    router = APIRouter()

    @router.get("/topics/{topic_id}/__boom_for_test")
    async def _boom(topic_id: str) -> dict:
        raise RuntimeError("kaboom in a route")

    app.include_router(router)
    added = app.router.routes[-1]
    yield
    app.router.routes.remove(added)


def _make_project(client) -> str:
    r = post_project(client, json={"name": "P"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _make_topic(client, project_id: str) -> str:
    r = client.post("/topics", json={"project_id": project_id, "title": "做一个东西"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _backend_events(client, topic_id: str) -> list[dict]:
    """The errors kept for `topic_id`, after checking the room says none."""
    r = client.get(f"/topics/{topic_id}/blocks")
    assert r.status_code == 200
    assert not [
        b
        for b in r.json()["data"]["data"]
        if (b.get("meta") or {}).get("event_type") == "backend_error"
    ]

    async def read() -> list[dict]:
        async with client.test_factory() as session:
            rows = await session.scalars(
                select(RunRecord)
                .where(RunRecord.kind == "backend_error")
                .order_by(RunRecord.created_at)
            )
            return [
                {"content": r.content, "meta": r.meta, "project": r.project_id}
                for r in rows
                if (r.meta or {}).get("conversation") == topic_id
            ]

    return client.portal.call(read)


def _post(client, errors, *, token=None, **body):
    headers = {"X-Cheese-Token": token} if token else None
    return client.post(
        "/backend-errors", json={**body, "errors": errors}, headers=headers
    )


# --- the push intake -------------------------------------------------------


def test_a_reported_error_is_kept_with_its_room_and_not_said_there(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)

    r = _post(
        client,
        [
            {
                "message": "nope",
                "exc_type": "ValueError",
                "stack": 'File "app/x.py", line 3, in go\nValueError: nope',
                "where": "POST /api/topics/x/chat",
                "request_id": "rid-1",
            }
        ],
        token=mint_scoped_token(project_id=pid, topic_id=tid),
    )
    assert r.status_code == 200
    assert r.json()["data"] == {"accepted": 1, "dropped": 0}

    events = _backend_events(client, tid)
    assert len(events) == 1
    assert events[0]["meta"]["event_type"] == "backend_error"
    assert "nope" in events[0]["content"]
    assert "\n" not in events[0]["content"]  # one line for a human…
    assert events[0]["meta"]["stack"].endswith("ValueError: nope")  # …whole for 芝士
    assert events[0]["meta"]["request_id"] == "rid-1"


def test_report_without_a_topic_falls_back_to_the_project_root(client):
    pid = _make_project(client)
    r = _post(
        client,
        [{"message": "boom"}],
        token=mint_scoped_token(project_id=pid),
    )
    assert r.status_code == 200
    assert r.json()["data"]["accepted"] == 1


def test_a_flood_through_the_endpoint_produces_one_record(client):
    """The acceptance criterion, end to end: 800 identical failures pushed in,
    one record kept."""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    token = mint_scoped_token(project_id=pid, topic_id=tid)
    err = {
        "message": "nope",
        "exc_type": "ValueError",
        "stack": 'File "app/x.py", line 3, in go\nValueError: nope',
    }

    accepted = 0
    for _ in range(80):  # 80 batches × 10 = 800 reports
        r = _post(client, [err] * 10, token=token)
        assert r.status_code == 200
        accepted += r.json()["data"]["accepted"]

    assert accepted == 1
    assert len(_backend_events(client, tid)) == 1


def test_distinct_errors_are_capped_per_project(client):
    """Even a project inventing a brand-new error every time cannot flood the
    records past the hourly ceiling."""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    token = mint_scoped_token(project_id=pid, topic_id=tid)

    for i in range(backend_log.MAX_BLOCKS_PER_HOUR + 25):
        _post(client, [{"message": f"distinct error {i!r}"}], token=token)

    assert len(_backend_events(client, tid)) == backend_log.MAX_BLOCKS_PER_HOUR


def test_dropped_report_still_returns_200(client):
    """A crashing reporter must never be told to retry — that would amplify the
    very flood the intake exists to stop."""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    token = mint_scoped_token(project_id=pid, topic_id=tid)
    err = {"message": "same"}

    assert _post(client, [err], token=token).json()["data"]["accepted"] == 1
    second = _post(client, [err], token=token)
    assert second.status_code == 200
    assert second.json()["data"] == {"accepted": 0, "dropped": 1}


def test_a_scoped_token_cannot_report_into_another_project(client):
    """The token names the room; the body does not get a vote."""
    mine = _make_project(client)
    my_topic = _make_topic(client, mine)
    theirs = _make_project(client)
    their_topic = _make_topic(client, theirs)

    r = _post(
        client,
        [{"message": "trespass"}],
        token=mint_scoped_token(project_id=mine, topic_id=my_topic),
        project_id=theirs,
        topic_id=their_topic,
    )
    assert r.status_code == 200
    assert _backend_events(client, their_topic) == []
    assert len(_backend_events(client, my_topic)) == 1


def test_unauthenticated_report_is_rejected(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    r = _post(client, [{"message": "x"}], token="not-a-token", topic_id=tid)
    assert r.status_code == 401
    assert _backend_events(client, tid) == []


def test_a_report_naming_no_real_project_is_kept_without_one(client):
    unknown = str(uuid.uuid4())
    r = _post(client, [{"message": "x"}], token=mint_scoped_token(project_id=unknown))
    assert r.status_code == 200

    async def read():
        async with client.test_factory() as session:
            return list(
                await session.scalars(
                    select(RunRecord.project_id).where(
                        RunRecord.kind == "backend_error"
                    )
                )
            )

    assert client.portal.call(read) == [None]


# --- the app reporting itself ---------------------------------------------


def test_an_unhandled_route_exception_is_kept_with_its_room(
    in_process_db, crashing_route
):
    client = in_process_db
    pid = _make_project(client)
    tid = _make_topic(client, pid)

    with pytest.raises(RuntimeError):
        client.get(f"/topics/{tid}/__boom_for_test")

    events = _backend_events(client, tid)
    assert len(events) == 1
    assert "RuntimeError" in events[0]["content"]
    assert "kaboom in a route" in events[0]["content"]
    assert (
        events[0]["meta"]["where"] == f"GET /topics/{tid}/__boom_for_test"
    )  # 服务端记录的是它实际收到的路径
    # The stack is kept from the TAIL: outer middleware frames are boilerplate,
    # the throw site is what identifies the bug.
    stack = events[0]["meta"]["stack"]
    assert "in _boom" in stack
    assert stack.endswith("RuntimeError: kaboom in a route\n")


def test_repeated_crashes_of_one_route_still_produce_one_record(
    in_process_db, crashing_route
):
    client = in_process_db
    pid = _make_project(client)
    tid = _make_topic(client, pid)

    for _ in range(25):
        with pytest.raises(RuntimeError):
            client.get(f"/topics/{tid}/__boom_for_test")

    assert len(_backend_events(client, tid)) == 1


def test_a_flood_that_stopped_gets_its_count_when_the_window_closes(
    in_process_db,
):
    """End to end for the case a recurrence-driven summary could never reach:
    800 failures, then silence because it was fixed. The records must still
    hold the number — that is what says how bad it was."""
    client = in_process_db
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    token = mint_scoped_token(project_id=pid, topic_id=tid)
    err = {"message": "nope", "exc_type": "ValueError"}

    for _ in range(80):
        _post(client, [err] * 10, token=token)
    assert len(_backend_events(client, tid)) == 1  # the flood itself stays one

    # …and then nothing else ever fails. The clock is what closes the window.
    written = client.portal.call(
        lambda: backend_log.flush_expired(
            now=time.time() + backend_log.DEDUP_WINDOW_S + 1
        )
    )

    assert written == 1
    events = _backend_events(client, tid)
    assert len(events) == 2
    summary = next(e for e in events if (e["meta"] or {}).get("summary"))
    assert summary["meta"]["count"] == 800
    assert "800" in summary["content"]
    assert "\n" not in summary["content"]


def test_a_window_too_young_to_expire_is_kept_when_the_process_goes(in_process_db):
    """`flush_all` is for the caller that stops existing (迁移顺序 2e).

    `flush_expired` is a clock and keeps nothing until 300 s have filled the
    window. A process handing over is not on that clock: whatever it collected
    in its last minutes is nobody else's — the next process holds its own
    windows, never this one's — so it is kept full window or not.
    """
    client = in_process_db
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    token = mint_scoped_token(project_id=pid, topic_id=tid)
    _post(client, [{"message": "nope", "exc_type": "ValueError"}] * 5, token=token)

    assert client.portal.call(backend_log.flush_expired) == 0
    assert len(_backend_events(client, tid)) == 1  # only the first report

    assert client.portal.call(backend_log.flush_all) == 1
    events = _backend_events(client, tid)
    assert len(events) == 2
    summary = next(e for e in events if (e["meta"] or {}).get("summary"))
    assert summary["meta"]["count"] == 5
    assert "5" in summary["content"]


def test_flushing_with_nothing_expired_writes_nothing(in_process_db):
    client = in_process_db
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    _post(
        client,
        [{"message": "x"}],
        token=mint_scoped_token(project_id=pid, topic_id=tid),
    )

    assert client.portal.call(backend_log.flush_expired) == 0
    assert len(_backend_events(client, tid)) == 1


def test_a_crash_in_a_topic_is_kept_with_the_request_id_the_browser_never_sent(
    in_process_db, crashing_route
):
    """A browser sends no `X-Request-ID`, so a record built from that header
    carried an empty id on every 5xx — the one field that ties the record to
    the request log line and to the id the caller was answered with. It is the
    request's own id that belongs here, minted when the caller did not name
    one, and in the shape this app mints and echoes."""
    client = in_process_db
    pid = _make_project(client)
    tid = _make_topic(client, pid)

    with pytest.raises(RuntimeError):
        client.get(f"/topics/{tid}/__boom_for_test")

    events = _backend_events(client, tid)
    assert len(events) == 1
    assert re.fullmatch(r"[0-9a-f]{12}", events[0]["meta"]["request_id"])


def test_a_crash_is_kept_with_the_request_id_the_caller_did_send(
    in_process_db, crashing_route
):
    """The other half of the same rule: a caller that names the request keeps
    that name — the id in the log and in the record is the one it can quote."""
    client = in_process_db
    pid = _make_project(client)
    tid = _make_topic(client, pid)

    with pytest.raises(RuntimeError):
        client.get(
            f"/topics/{tid}/__boom_for_test", headers={"X-Request-ID": "rid-abc"}
        )

    events = _backend_events(client, tid)
    assert len(events) == 1
    assert events[0]["meta"]["request_id"] == "rid-abc"


def test_a_failure_inside_the_intake_is_not_reported_back_into_it(client, monkeypatch):
    """The intake is the one route that must never report into itself: its own
    failure would be pushed straight back at the intake that just failed, on
    every retry, for as long as it stays broken.

    Reached through the real stack on purpose — the request the reporter
    middleware inspects carries the path THIS app was handed (`/backend-errors`),
    which is what the guard has to compare against, not the public
    `/api/backend-errors` a caller types.
    """
    reported = AsyncMock()
    monkeypatch.setattr(backend_log, "report_request_failure", reported)

    def _broken_intake(*_args, **_kwargs):
        raise RuntimeError("intake is broken")

    monkeypatch.setattr("app.api.routes.backend_log._room", _broken_intake)

    with pytest.raises(RuntimeError):
        _post(client, [{"message": "x"}])

    assert not reported.called


def test_expected_4xx_is_not_an_incident(in_process_db):
    """Business-expected errors are normal flow. A 404 from a real route is
    not kept."""
    client = in_process_db
    pid = _make_project(client)
    tid = _make_topic(client, pid)

    r = client.get(f"/topics/{uuid.uuid4()}/blocks")
    assert r.status_code == 404
    assert _backend_events(client, tid) == []
