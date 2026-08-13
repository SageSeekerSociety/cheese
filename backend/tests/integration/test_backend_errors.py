"""The backend half of 现场 error reporting.

Two ways in, one intake:
- ``POST /api/backend-errors`` — a process that crashed pushes its own failure.
- the ``report_unhandled_to_room`` middleware — this app pushing ITSELF.

Both must land as event blocks a human reads as one line and 芝士 reads whole.
"""

import time
import uuid

import pytest
from fastapi import APIRouter

from app.core.sandbox_auth import mint_scoped_token
from app.domain import backend_log
from app.main import app


@pytest.fixture(autouse=True)
def _fresh_intake(monkeypatch):
    """The intake singleton dedups for the process lifetime — isolate tests."""
    monkeypatch.setattr(backend_log, "intake", backend_log.BackendErrorIntake())


@pytest.fixture
def in_process_db(client, monkeypatch):
    """The middleware opens its OWN session (the request's is already broken), so
    point that factory at the same database this client reads from."""
    monkeypatch.setattr(backend_log, "async_session_factory", client.test_factory)
    return client


@pytest.fixture
def crashing_route(client):
    """A route that raises for real, so the middleware is exercised through the
    actual stack rather than by calling it directly."""
    router = APIRouter()

    @router.get("/api/topics/{topic_id}/__boom_for_test")
    async def _boom(topic_id: str) -> dict:
        raise RuntimeError("kaboom in a route")

    app.include_router(router)
    added = app.router.routes[-1]
    yield
    app.router.routes.remove(added)


def _make_project(client) -> str:
    r = client.post("/api/projects", json={"name": "P"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _make_topic(client, project_id: str) -> str:
    r = client.post(
        "/api/topics", json={"project_id": project_id, "title": "做一个东西"}
    )
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _backend_events(client, topic_id: str) -> list[dict]:
    r = client.get(f"/api/topics/{topic_id}/blocks")
    assert r.status_code == 200
    return [
        b
        for b in r.json()["data"]["data"]
        if (b.get("meta") or {}).get("event_type") == "backend_error"
    ]


def _post(client, errors, *, token=None, **body):
    headers = {"X-Cheese-Token": token} if token else None
    return client.post(
        "/api/backend-errors", json={**body, "errors": errors}, headers=headers
    )


# --- the push intake -------------------------------------------------------


def test_reported_error_lands_in_the_topic_timeline(client):
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
    assert "💥" in events[0]["content"]
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


def test_a_flood_through_the_endpoint_produces_one_block(client):
    """The acceptance criterion, end to end: 800 identical failures pushed in,
    a room that stays readable."""
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
    room past the hourly ceiling."""
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


def test_unknown_room_is_404(client):
    r = _post(
        client,
        [{"message": "x"}],
        token=mint_scoped_token(project_id=str(uuid.uuid4())),
    )
    assert r.status_code == 404


# --- the app reporting itself ---------------------------------------------


def test_unhandled_route_exception_becomes_a_block_in_that_topic(
    in_process_db, crashing_route
):
    client = in_process_db
    pid = _make_project(client)
    tid = _make_topic(client, pid)

    with pytest.raises(RuntimeError):
        client.get(f"/api/topics/{tid}/__boom_for_test")

    events = _backend_events(client, tid)
    assert len(events) == 1
    assert "RuntimeError" in events[0]["content"]
    assert "kaboom in a route" in events[0]["content"]
    assert events[0]["meta"]["where"] == f"GET /api/topics/{tid}/__boom_for_test"
    # The stack is kept from the TAIL: outer middleware frames are boilerplate,
    # the throw site is what identifies the bug.
    stack = events[0]["meta"]["stack"]
    assert "in _boom" in stack
    assert stack.endswith("RuntimeError: kaboom in a route\n")


def test_repeated_crashes_of_one_route_still_produce_one_block(
    in_process_db, crashing_route
):
    client = in_process_db
    pid = _make_project(client)
    tid = _make_topic(client, pid)

    for _ in range(25):
        with pytest.raises(RuntimeError):
            client.get(f"/api/topics/{tid}/__boom_for_test")

    assert len(_backend_events(client, tid)) == 1


async def test_a_flood_that_stopped_gets_its_count_when_the_window_closes(
    in_process_db,
):
    """End to end for the case a recurrence-driven summary could never reach:
    800 failures, then silence because it was fixed. The room must still learn
    the number — that is what says how bad it was."""
    client = in_process_db
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    token = mint_scoped_token(project_id=pid, topic_id=tid)
    err = {"message": "nope", "exc_type": "ValueError"}

    for _ in range(80):
        _post(client, [err] * 10, token=token)
    assert len(_backend_events(client, tid)) == 1  # the flood itself stays one line

    # …and then nothing else ever fails. The clock is what closes the window.
    written = await backend_log.flush_expired(
        now=time.time() + backend_log.DEDUP_WINDOW_S + 1
    )

    assert written == 1
    events = _backend_events(client, tid)
    assert len(events) == 2
    summary = next(e for e in events if (e["meta"] or {}).get("summary"))
    assert summary["meta"]["count"] == 800
    assert "800" in summary["content"]
    assert "\n" not in summary["content"]


async def test_flushing_with_nothing_expired_writes_nothing(in_process_db):
    client = in_process_db
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    _post(
        client,
        [{"message": "x"}],
        token=mint_scoped_token(project_id=pid, topic_id=tid),
    )

    assert await backend_log.flush_expired() == 0
    assert len(_backend_events(client, tid)) == 1


def test_expected_4xx_is_not_an_incident(in_process_db):
    """Business-expected errors are normal flow. A 404 from a real route must
    leave the room untouched."""
    client = in_process_db
    pid = _make_project(client)
    tid = _make_topic(client, pid)

    r = client.get(f"/api/topics/{uuid.uuid4()}/blocks")
    assert r.status_code == 404
    assert _backend_events(client, tid) == []
