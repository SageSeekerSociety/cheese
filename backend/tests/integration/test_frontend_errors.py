"""POST /api/frontend-errors — browser errors become 现场 event blocks, so
agents (who can't read a user's console) can debug them from the timeline."""

import uuid

import pytest

from app.domain import frontend_log


@pytest.fixture(autouse=True)
def _fresh_intake(monkeypatch):
    """The intake singleton dedups for the process lifetime — isolate tests."""
    monkeypatch.setattr(frontend_log, "intake", frontend_log.FrontendErrorIntake())


def _make_project(client) -> str:
    r = client.post("/projects", json={"name": "P"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _make_topic(client, project_id: str) -> str:
    r = client.post("/topics", json={"project_id": project_id, "title": "做一个东西"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _topic_events(client, topic_id: str) -> list[dict]:
    r = client.get(f"/topics/{topic_id}/blocks")
    assert r.status_code == 200
    return [
        b
        for b in r.json()["data"]["data"]
        if (b.get("meta") or {}).get("event_type") == "frontend_error"
    ]


def test_errors_land_in_topic_timeline(client):
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
    """The timeline has had these errors all along; what it has never had is a
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
