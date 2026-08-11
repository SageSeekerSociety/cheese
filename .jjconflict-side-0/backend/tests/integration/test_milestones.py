"""Integration tests for the milestone + calendar domain (spec §7.2)."""

from datetime import UTC, datetime, timedelta

NIL_UUID = "00000000-0000-0000-0000-000000000000"


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _create_project(client, name: str = "Demo") -> str:
    r = client.post("/api/projects", json={"name": name})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _add_milestone(client, project_id: str, **body) -> dict:
    r = client.post(f"/api/projects/{project_id}/milestones", json=body)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_overdue_milestone_becomes_missed_and_leaves_calendar(client):
    # spec §7.2: an overdue milestone is 'missed', not 'upcoming/临近'.
    pid = _create_project(client)
    now = datetime.now(UTC)
    past = _add_milestone(
        client, pid, title="已逾期", due_date=_iso(now - timedelta(days=3))
    )
    future = _add_milestone(
        client, pid, title="未来", due_date=_iso(now + timedelta(days=5))
    )

    # Calendar (countdown) only shows the genuinely future one.
    cal = client.get(f"/api/projects/{pid}/calendar").json()["data"]["data"]
    titles = [m["title"] for m in cal]
    assert titles == ["未来"]
    assert cal[0]["id"] == future["id"]

    # The full list shows the overdue one flipped to missed.
    allm = client.get(f"/api/projects/{pid}/milestones").json()["data"]["data"]
    by_title = {m["title"]: m for m in allm}
    assert by_title["已逾期"]["status"] == "missed"
    assert by_title["未来"]["status"] == "upcoming"
    assert past["status"] == "upcoming"  # was upcoming at creation


def test_create_milestone(client):
    project_id = _create_project(client)
    data = _add_milestone(
        client,
        project_id,
        title="中期检查",
        description="提交中期报告",
        due_date="2026-09-01T09:00:00Z",
    )
    assert data["title"] == "中期检查"
    assert data["description"] == "提交中期报告"
    assert data["status"] == "upcoming"
    assert data["due_date"].startswith("2026-09-01")
    assert data["project_id"] == project_id
    assert data["auto_pinned"] is False


def test_create_milestone_unknown_project_404(client):
    r = client.post(f"/api/projects/{NIL_UUID}/milestones", json={"title": "x"})
    assert r.status_code == 404


def test_list_orders_by_due_date_nulls_last(client):
    project_id = _create_project(client)
    _add_milestone(client, project_id, title="later", due_date="2026-12-01T00:00:00Z")
    _add_milestone(client, project_id, title="no-date")
    _add_milestone(client, project_id, title="sooner", due_date="2026-06-01T00:00:00Z")

    r = client.get(f"/api/projects/{project_id}/milestones")
    body = r.json()
    assert body["code"] == 200
    assert body["data"]["total"] == 3
    titles = [m["title"] for m in body["data"]["data"]]
    assert titles == ["sooner", "later", "no-date"]


def test_list_unknown_project_404(client):
    r = client.get(f"/api/projects/{NIL_UUID}/milestones")
    assert r.status_code == 404


def test_calendar_filters_upcoming_and_dated(client):
    project_id = _create_project(client)
    # Dates relative to now so the test isn't brittle as the calendar rolls over.
    now = datetime.now(UTC)
    # Has date, upcoming -> shown (a sorts before b)
    _add_milestone(
        client, project_id, title="b", due_date=_iso(now + timedelta(days=40))
    )
    _add_milestone(
        client, project_id, title="a", due_date=_iso(now + timedelta(days=10))
    )
    # Upcoming but no date -> hidden
    _add_milestone(client, project_id, title="no-date")
    # Dated but marked done -> hidden
    done = _add_milestone(
        client, project_id, title="done", due_date=_iso(now - timedelta(days=30))
    )
    r = client.put(f"/api/milestones/{done['id']}", json={"status": "done"})
    assert r.status_code == 200

    r = client.get(f"/api/projects/{project_id}/calendar")
    body = r.json()
    assert body["code"] == 200
    titles = [m["title"] for m in body["data"]["data"]]
    assert titles == ["a", "b"]
    assert body["data"]["total"] == 2


def test_calendar_unknown_project_404(client):
    r = client.get(f"/api/projects/{NIL_UUID}/calendar")
    assert r.status_code == 404


def test_update_status_and_fields(client):
    project_id = _create_project(client)
    m = _add_milestone(client, project_id, title="t", due_date="2026-09-01T00:00:00Z")

    r = client.put(
        f"/api/milestones/{m['id']}",
        json={"title": "结题", "status": "missed"},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["title"] == "结题"
    assert data["status"] == "missed"
    # due_date untouched when not in body
    assert data["due_date"].startswith("2026-09-01")


def test_update_can_clear_due_date(client):
    project_id = _create_project(client)
    m = _add_milestone(client, project_id, title="t", due_date="2026-09-01T00:00:00Z")

    r = client.put(f"/api/milestones/{m['id']}", json={"due_date": None})
    assert r.status_code == 200
    assert r.json()["data"]["due_date"] is None


def test_update_unknown_404(client):
    r = client.put(f"/api/milestones/{NIL_UUID}", json={"title": "x"})
    assert r.status_code == 404


def test_delete_milestone(client):
    project_id = _create_project(client)
    m = _add_milestone(client, project_id, title="t")

    r = client.delete(f"/api/milestones/{m['id']}")
    assert r.status_code == 200

    r = client.get(f"/api/projects/{project_id}/milestones")
    assert r.json()["data"]["total"] == 0


def test_delete_unknown_404(client):
    r = client.delete(f"/api/milestones/{NIL_UUID}")
    assert r.status_code == 404
