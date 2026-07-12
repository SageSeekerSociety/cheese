"""Integration tests for the 团队↔题目 匹配市场 (spec §13 阶段 6).

Full chain through the REST API: publish/unpublish a template, browse the
market (with keyword filter), apply with a project, Space accepts (→ Task +
ProjectTaskLink + notification) or declines. State machine and idempotency.
"""

import uuid

from tests.conftest import seed_space


def _make_space(client, name: str = "信院") -> int:
    # The cheesex POST /api/spaces uuid stub was retired (fusion unify P1b/c); the
    # task-template market now lives on a 知是 int Space, seeded directly.
    return seed_space(client, name)


def _make_template(client, space_id: str, name: str = "创研课 2026 秋") -> str:
    r = client.post(
        f"/api/spaces/{space_id}/templates",
        json={
            "name": name,
            "description": "AI 全过程项目",
            "resource_pack": {"gpu_hours": 100},
            "conditions": [{"required_topic": "结题答辩"}],
            "default_role": "mentor",
        },
    )
    return r.json()["data"]["id"]


def _make_project(client, name: str = "推荐系统小队") -> str:
    r = client.post("/api/projects", json={"name": name, "owner_handle": "lead-1"})
    return r.json()["data"]["id"]


def _publish(client, space_id: str, template_id: str) -> dict:
    r = client.post(f"/api/spaces/{space_id}/templates/{template_id}/publish")
    assert r.status_code == 200
    return r.json()["data"]


def _apply(client, template_id: str, project_id: str, pitch: str = "我们很合适"):
    return client.post(
        f"/api/market/tasks/{template_id}/apply",
        json={"project_id": project_id, "pitch": pitch},
    )


def test_publish_unpublish_and_market_list(client):
    space_id = _make_space(client)
    template_id = _make_template(client, space_id)
    hidden_id = _make_template(client, space_id, name="未发布的活动")
    assert hidden_id  # created but never published

    # Fresh template is unpublished and absent from the market.
    r = client.get(f"/api/templates/{template_id}")
    assert r.json()["data"]["published"] is False
    assert client.get("/api/market/tasks").json()["data"]["total"] == 0

    # Publish → listed with space name + protocol summary.
    data = _publish(client, space_id, template_id)
    assert data["published"] is True
    body = client.get("/api/market/tasks").json()["data"]
    assert body["total"] == 1
    listing = body["data"][0]
    assert listing["id"] == template_id
    assert listing["space_name"] == "信院"
    assert listing["resource_pack"] == {"gpu_hours": 100}
    assert listing["conditions"] == [{"required_topic": "结题答辩"}]
    assert listing["default_role"] == "mentor"

    # Unpublish → gone from the market.
    r = client.post(f"/api/spaces/{space_id}/templates/{template_id}/unpublish")
    assert r.json()["data"]["published"] is False
    assert client.get("/api/market/tasks").json()["data"]["total"] == 0


def test_publish_scoped_to_owning_space(client):
    space_id = _make_space(client)
    other_space = _make_space(client, name="书院")
    template_id = _make_template(client, space_id)
    r = client.post(f"/api/spaces/{other_space}/templates/{template_id}/publish")
    assert r.status_code == 404


def test_market_keyword_filter(client):
    space_id = _make_space(client, name="信院")
    t1 = _make_template(client, space_id, name="创研课 2026 秋")
    t2 = _make_template(client, space_id, name="黑客松第三期")
    _publish(client, space_id, t1)
    _publish(client, space_id, t2)

    body = client.get("/api/market/tasks", params={"q": "黑客松"}).json()["data"]
    assert body["total"] == 1
    assert body["data"][0]["id"] == t2

    # Space name matches too; LIKE wildcards in the keyword match literally.
    assert (
        client.get("/api/market/tasks", params={"q": "信院"}).json()["data"]["total"]
        == 2
    )
    assert (
        client.get("/api/market/tasks", params={"q": "%"}).json()["data"]["total"] == 0
    )
    assert (
        client.get("/api/market/tasks", params={"q": "没有这个"}).json()["data"][
            "total"
        ]
        == 0
    )


def test_apply_pending_and_idempotent(client):
    space_id = _make_space(client)
    template_id = _make_template(client, space_id)
    _publish(client, space_id, template_id)
    project_id = _make_project(client)

    r = _apply(client, template_id, project_id)
    assert r.status_code == 200
    application = r.json()["data"]
    assert application["status"] == "pending"
    assert application["pitch"] == "我们很合适"
    assert application["template_id"] == template_id
    assert application["project_id"] == project_id
    assert application["decided_by"] is None
    assert application["task_id"] is None

    # Re-applying with the same project is idempotent: same application row.
    r2 = _apply(client, template_id, project_id, pitch="换个说法")
    assert r2.status_code == 200
    assert r2.json()["data"]["id"] == application["id"]
    assert r2.json()["data"]["pitch"] == "我们很合适"  # original pitch kept

    # Space side sees the applicant with its project name.
    rows = client.get(f"/api/market/tasks/{template_id}/applications").json()["data"]
    assert rows["total"] == 1
    assert rows["data"][0]["project_name"] == "推荐系统小队"


def test_apply_guards(client):
    space_id = _make_space(client)
    template_id = _make_template(client, space_id)
    project_id = _make_project(client)

    # Unpublished template → 422; missing template/project → 404.
    assert _apply(client, template_id, project_id).status_code == 422
    assert _apply(client, str(uuid.uuid4()), project_id).status_code == 404
    _publish(client, space_id, template_id)
    assert _apply(client, template_id, str(uuid.uuid4())).status_code == 404


def test_accept_creates_task_link_and_notification(client):
    space_id = _make_space(client)
    template_id = _make_template(client, space_id)
    _publish(client, space_id, template_id)
    project_id = _make_project(client)
    application_id = _apply(client, template_id, project_id).json()["data"]["id"]

    r = client.post(
        f"/api/market/applications/{application_id}/accept",
        json={"decided_by": "prof-zhang"},
    )
    assert r.status_code == 200
    decided = r.json()["data"]
    assert decided["status"] == "accepted"
    assert decided["decided_by"] == "prof-zhang"
    assert decided["decided_at"] is not None
    task_id = decided["task_id"]
    assert task_id is not None

    # A Task now exists under the template, named after the project.
    tasks = client.get(f"/api/templates/{template_id}/tasks").json()["data"]
    assert tasks["total"] == 1
    assert tasks["data"][0]["id"] == task_id
    assert tasks["data"][0]["title"] == "推荐系统小队"

    # The project is linked to that task (protocol signed, §4.2).
    links = client.get(f"/api/projects/{project_id}/tasks").json()["data"]
    assert links["total"] == 1
    assert links["data"][0]["task_id"] == task_id

    # The project got a notification carrying the structured payload.
    notes = client.get(f"/api/projects/{project_id}/notifications").json()["data"]
    matching = [
        n for n in notes["data"] if n["payload"].get("application_id") == application_id
    ]
    assert len(matching) == 1
    assert matching[0]["kind"] == "change_alert"
    assert matching[0]["payload"]["task_id"] == task_id

    # Accepting again is idempotent: no second task/link/notification.
    r2 = client.post(
        f"/api/market/applications/{application_id}/accept",
        json={"decided_by": "prof-li"},
    )
    assert r2.json()["data"]["decided_by"] == "prof-zhang"
    assert (
        client.get(f"/api/templates/{template_id}/tasks").json()["data"]["total"] == 1
    )
    assert client.get(f"/api/projects/{project_id}/tasks").json()["data"]["total"] == 1
    notes2 = client.get(f"/api/projects/{project_id}/notifications").json()["data"]
    assert (
        len(
            [
                n
                for n in notes2["data"]
                if n["payload"].get("application_id") == application_id
            ]
        )
        == 1
    )


def test_accept_inherits_default_role(client):
    space_id = _make_space(client)
    template_id = _make_template(client, space_id)
    _publish(client, space_id, template_id)
    # Project created without an expert role → inherits the template's default.
    project_id = client.post("/api/projects", json={"name": "小队"}).json()["data"][
        "id"
    ]
    application_id = _apply(client, template_id, project_id).json()["data"]["id"]
    client.post(f"/api/market/applications/{application_id}/accept", json={})
    project = client.get(f"/api/projects/{project_id}").json()["data"]
    assert project["expert_role"] == "mentor"


def test_decline_and_terminal_states(client):
    space_id = _make_space(client)
    template_id = _make_template(client, space_id)
    _publish(client, space_id, template_id)
    project_id = _make_project(client)
    application_id = _apply(client, template_id, project_id).json()["data"]["id"]

    r = client.post(
        f"/api/market/applications/{application_id}/decline",
        json={"decided_by": "prof-zhang"},
    )
    assert r.status_code == 200
    declined = r.json()["data"]
    assert declined["status"] == "declined"
    assert declined["decided_by"] == "prof-zhang"
    assert declined["task_id"] is None

    # No task, no link; the project is told (light notification).
    assert (
        client.get(f"/api/templates/{template_id}/tasks").json()["data"]["total"] == 0
    )
    assert client.get(f"/api/projects/{project_id}/tasks").json()["data"]["total"] == 0
    notes = client.get(f"/api/projects/{project_id}/notifications").json()["data"]
    assert any(
        n["payload"].get("application_id") == application_id for n in notes["data"]
    )

    # Terminal: declining again is a no-op; accepting a declined one is 422.
    r2 = client.post(f"/api/market/applications/{application_id}/decline", json={})
    assert r2.json()["data"]["decided_by"] == "prof-zhang"
    r3 = client.post(f"/api/market/applications/{application_id}/accept", json={})
    assert r3.status_code == 422

    # And the reverse: declining an accepted application is 422.
    other_project = _make_project(client, name="另一队")
    other_app = _apply(client, template_id, other_project).json()["data"]["id"]
    client.post(f"/api/market/applications/{other_app}/accept", json={})
    r4 = client.post(f"/api/market/applications/{other_app}/decline", json={})
    assert r4.status_code == 422


def test_decide_missing_application_404(client):
    r = client.post(f"/api/market/applications/{uuid.uuid4()}/accept", json={})
    assert r.status_code == 404
    r = client.post(f"/api/market/applications/{uuid.uuid4()}/decline", json={})
    assert r.status_code == 404


def test_applications_list_missing_template_404(client):
    r = client.get(f"/api/market/tasks/{uuid.uuid4()}/applications")
    assert r.status_code == 404
