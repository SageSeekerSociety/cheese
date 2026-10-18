"""Rewards and manual purchases issue real packs exactly once."""

import pytest

from app.core.config import settings
from tests.integration.test_credit_plans import _auth, _shared_team


@pytest.fixture
def actors(client, monkeypatch):
    monkeypatch.setattr(settings, "platform_admin_handles", ["rewards-admin"])
    monkeypatch.setattr(settings, "feedback_triage_handles", ["rewards-triager"])
    return {
        name: _auth(client, f"rewards-{name}")
        for name in ("owner", "admin", "triager", "outsider")
    }


def _packs(client, headers):
    result = client.get("/users/me/credits/usage", headers=headers)
    assert result.status_code == 200, result.text
    return result.json()["data"]["packs"]


def test_beta_credits_are_claimed_once_and_go_to_the_personal_balance(client, actors):
    headers = actors["owner"]
    assert client.get("/users/me/credits/beta", headers=headers).json()["data"] == {
        "credits": 500,
        "claimed": False,
        "feedback_credits": 100,
    }
    first = client.post("/users/me/credits/beta", headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["data"] == {"credits": 500, "issued": True}
    assert (
        client.post("/users/me/credits/beta", headers=headers).json()["data"]["issued"]
        is False
    )
    assert sum(p["credits_total"] for p in _packs(client, headers)) == 500
    assert (
        client.get("/users/me/credits/beta", headers=headers).json()["data"]["claimed"]
        is True
    )
    assert (
        client.post("/users/me/credits/beta", headers={"Authorization": ""}).status_code
        == 401
    )


def test_feedback_rewards_require_triage_and_are_not_repeatable(client, actors):
    report = client.post(
        "/feedback",
        headers=actors["owner"],
        json={"title": "A useful report", "visibility": "private"},
    )
    assert report.status_code == 200, report.text
    feedback_id = report.json()["data"]["id"]
    url = f"/admin/feedback/{feedback_id}/reward"
    assert client.post(url, headers=actors["owner"]).status_code == 403
    assert client.post(url, headers=actors["admin"]).status_code == 403
    awarded = client.post(url, headers=actors["triager"])
    assert awarded.status_code == 200, awarded.text
    assert awarded.json()["data"] == {"credits": 100, "issued": True}
    assert client.post(url, headers=actors["triager"]).json()["data"]["issued"] is False
    assert sum(p["credits_total"] for p in _packs(client, actors["owner"])) == 100
    detail = client.get(f"/feedback/{feedback_id}", headers=actors["owner"]).json()[
        "data"
    ]
    assert detail["reward"]["credits"] == 100
    assert detail["status"] == "in_progress"
    assert (
        client.get(f"/feedback/{feedback_id}", headers=actors["outsider"]).status_code
        == 404
    )


def test_top_up_is_an_enquiry_until_admin_confirms_and_issues_once(client, actors):
    team = _shared_team(client, actors["owner"], "rewards-lab")
    url = f"/teams/{team}/credits/requests"
    assert (
        client.post(
            url, headers=actors["outsider"], json={"kind": "top_up", "credits": 1000}
        ).status_code
        == 403
    )
    assert (
        client.post(url, headers=actors["owner"], json={"kind": "top_up"}).status_code
        == 400
    )
    response = client.post(
        url, headers=actors["owner"], json={"kind": "top_up", "credits": 1000}
    )
    assert response.status_code == 200, response.text
    request_id = response.json()["data"]["id"]
    assert (
        client.post(
            url, headers=actors["owner"], json={"kind": "top_up", "credits": 1000}
        ).status_code
        == 400
    )
    before = client.get(f"/admin/teams/{team}", headers=actors["admin"]).json()["data"]
    assert before["packs"] == []
    approve = f"/admin/credits/requests/{request_id}"
    assert (
        client.post(
            approve,
            headers=actors["owner"],
            json={"approve": True, "payment_confirmed": True},
        ).status_code
        == 403
    )
    assert (
        client.post(
            approve, headers=actors["admin"], json={"approve": True}
        ).status_code
        == 400
    )
    for _ in range(2):
        result = client.post(
            approve,
            headers=actors["admin"],
            json={"approve": True, "payment_confirmed": True},
        )
        assert result.status_code == 200, result.text
        assert result.json()["data"]["status"] == "completed"
    after = client.get(f"/admin/teams/{team}", headers=actors["admin"]).json()["data"]
    assert [(p["source"], p["credits_total"]) for p in after["packs"]] == [
        ("purchase", 1000)
    ]
    assert (
        client.get(url, headers=actors["owner"]).json()["data"]["items"][0]["status"]
        == "completed"
    )


def test_team_plan_requires_an_agreed_real_plan(client, actors):
    team = _shared_team(client, actors["owner"], "plan-lab")
    response = client.post(
        f"/teams/{team}/credits/requests",
        headers=actors["owner"],
        json={"kind": "team_plan"},
    )
    assert response.status_code == 200, response.text
    request_id = response.json()["data"]["id"]
    url = f"/admin/credits/requests/{request_id}"
    assert (
        client.post(
            url,
            headers=actors["admin"],
            json={"approve": True, "payment_confirmed": True},
        ).status_code
        == 400
    )
    plan = client.post(
        "/admin/plans",
        headers=actors["admin"],
        json={
            "key": "research",
            "name": "Team",
            "audience": "team",
            "credits_per_period": 5000,
        },
    )
    assert plan.status_code == 201, plan.text
    result = client.post(
        url,
        headers=actors["admin"],
        json={"approve": True, "payment_confirmed": True, "plan_key": "research"},
    )
    assert result.status_code == 200, result.text
    assert (
        client.get(f"/admin/teams/{team}", headers=actors["admin"]).json()["data"][
            "plan"
        ]["key"]
        == "research"
    )
