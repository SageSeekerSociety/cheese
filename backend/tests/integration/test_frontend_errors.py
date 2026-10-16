"""POST /api/frontend-errors — browser errors are kept as run records for the
admin page, with the room that was open, and never said in that room."""

import uuid

import pytest
from sqlalchemy import select

from app.domain import frontend_log
from app.domain.run_record.models import RunRecord
from tests.integration.conftest import post_project


@pytest.fixture(autouse=True)
def _fresh_intake(monkeypatch):
    """The intake singleton dedups for the process lifetime — isolate tests."""
    monkeypatch.setattr(frontend_log, "intake", frontend_log.FrontendErrorIntake())


def _make_project(client) -> str:
    r = post_project(client, json={"name": "P"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _make_topic(client, project_id: str) -> str:
    r = client.post("/topics", json={"project_id": project_id, "title": "做一个东西"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _topic_events(client, topic_id: str) -> list[dict]:
    """The errors kept for `topic_id`, after checking the room says none."""
    r = client.get(f"/topics/{topic_id}/blocks")
    assert r.status_code == 200
    assert not [
        b
        for b in r.json()["data"]["data"]
        if (b.get("meta") or {}).get("event_type") == "frontend_error"
    ]

    async def read() -> list[dict]:
        async with client.test_factory() as session:
            rows = await session.scalars(
                select(RunRecord).where(RunRecord.kind == "frontend_error")
            )
            return [
                {"content": r.content, "meta": r.meta}
                for r in rows
                if (r.meta or {}).get("conversation") == topic_id
            ]

    return client.portal.call(read)


def test_errors_are_kept_with_the_open_room_and_not_said_there(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)

    r = client.post(
        "/frontend-errors",
        json={
            "project_id": pid,
            "topic_id": tid,
            "errors": [
                {
                    "message": "boom",
                    "stack": "Error: boom\n  at a.js:1",
                    "source": "a.js:1",
                    "page": "/project/x",
                },
                {"message": "other"},
            ],
        },
    )
    assert r.status_code == 200
    assert r.json()["data"] == {"accepted": 2, "dropped": 0}

    events = _topic_events(client, tid)
    assert len(events) == 2
    boom = next(e for e in events if "boom" in e["content"])
    assert boom["meta"]["event_type"] == "frontend_error"
    assert boom["meta"]["stack"] == "Error: boom\n  at a.js:1"


def test_duplicates_collapse(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    err = {"message": "same", "stack": "Error: same\n  at x.js:1"}

    r = client.post(
        "/frontend-errors",
        json={"project_id": pid, "topic_id": tid, "errors": [err, err]},
    )
    assert r.json()["data"] == {"accepted": 1, "dropped": 1}

    r = client.post(
        "/frontend-errors",
        json={"project_id": pid, "topic_id": tid, "errors": [err]},
    )
    assert r.json()["data"] == {"accepted": 0, "dropped": 1}
    assert len(_topic_events(client, tid)) == 1


def test_unknown_project_404(client):
    r = client.post(
        "/frontend-errors",
        json={"project_id": str(uuid.uuid4()), "errors": [{"message": "x"}]},
    )
    assert r.status_code == 404


def test_a_new_error_reaches_a_person_and_a_repeat_does_not(client, monkeypatch):
    """The records have had these errors all along; what they have never had is a
    reader. An alert is that reader — but only for something not seen before,
    because the same error repeats hundreds of times a second in a render loop
    and a channel that receives all of them is muted by the end of the day."""
    from app.core import alerting

    sent: list[str] = []
    monkeypatch.setattr(
        alerting.settings, "feishu_alert_webhook", "https://example/hook"
    )
    monkeypatch.setattr(alerting, "budget", alerting._Budget())
    monkeypatch.setattr(alerting, "send", lambda title, lines: sent.append(title))

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    body = {
        "project_id": pid,
        "topic_id": tid,
        "errors": [{"message": "boom", "stack": "at f (a.js:1:1)", "page": "/x"}],
    }

    assert client.post("/frontend-errors", json=body).status_code == 200
    assert sent == ["前端报错：boom"]

    # Same fingerprint again: the intake drops it, so nobody is told twice.
    assert client.post("/frontend-errors", json=body).status_code == 200
    assert sent == ["前端报错：boom"]


@pytest.fixture
def alerted(monkeypatch) -> list[str]:
    from app.core import alerting

    sent: list[str] = []
    monkeypatch.setattr(
        alerting.settings, "feishu_alert_webhook", "https://example/hook"
    )
    monkeypatch.setattr(alerting, "send", lambda title, lines: sent.append(title))
    return sent


def test_a_stale_build_chunk_is_kept_but_not_alerted(client, alerted):
    """A tab on the previous build asks for a chunk the deploy removed, and the
    page reloads itself. Nobody can act on that, so nobody is told; the record
    still says it happened."""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    message = (
        "Unhandled rejection: Failed to fetch dynamically imported module: "
        "https://example.test/assets/ProjectSidebar-DKA_rx1A.js"
    )
    body = {"project_id": pid, "topic_id": tid, "errors": [{"message": message}]}

    assert client.post("/frontend-errors", json=body).status_code == 200

    assert alerted == []
    assert len(_topic_events(client, tid)) == 1


def test_a_dev_servers_errors_are_kept_but_not_alerted(client, alerted):
    """A developer's local server proxying to this backend reports the errors of
    code that may never ship. A user's desktop app is not that, and still
    alerts."""
    pid = _make_project(client)
    tid = _make_topic(client, pid)

    def report(message: str, origin: str) -> None:
        body = {"project_id": pid, "topic_id": tid, "errors": [{"message": message}]}
        r = client.post("/frontend-errors", json=body, headers={"Origin": origin})
        assert r.status_code == 200

    report("from vite", "http://127.0.0.1:3000")
    report("from localhost", "http://localhost:5317")
    report("from the desktop app", "http://tauri.localhost")
    report("from the site", "https://okcheese.example")

    assert alerted == ["前端报错：from the desktop app", "前端报错：from the site"]
    assert len(_topic_events(client, tid)) == 4
