"""Rewards and manual purchases issue real packs exactly once."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.domain.usage.models import CreditReward
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


def test_every_reward_pack_can_be_spent_for_a_year(client, actors):
    """The beta claim and an accepted report expire on the same rule: a year
    after they were issued."""
    report = client.post(
        "/feedback",
        headers=actors["owner"],
        json={"title": "Another useful report", "visibility": "private"},
    )
    assert report.status_code == 200, report.text
    before = datetime.now(UTC)
    assert client.post("/users/me/credits/beta", headers=actors["owner"]).is_success
    rewarded = client.post(
        f"/admin/feedback/{report.json()['data']['id']}/reward",
        headers=actors["triager"],
    )
    assert rewarded.status_code == 200, rewarded.text
    after = datetime.now(UTC)

    packs = _packs(client, actors["owner"])
    assert len(packs) == 2
    for pack in packs:
        expires = datetime.fromisoformat(pack["expires_at"])
        assert before + timedelta(days=365) <= expires <= after + timedelta(days=365)


def test_feedback_rewards_require_triage_and_are_not_repeatable(
    client, _portal, actors
):
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

    async def points() -> list[int]:
        async with client.test_factory() as session:
            return list(
                await session.scalars(
                    select(CreditReward.points).where(
                        CreditReward.key.like("feedback:%")
                    )
                )
            )

    # Contribution points are no longer shown, but still recorded.
    assert _portal.call(points) == [100]
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


def test_reward_history_belongs_only_to_the_authenticated_person(client, actors):
    assert client.get("/users/me/credits/rewards").status_code == 401
    client.post("/users/me/credits/beta", headers=actors["owner"])
    report = client.post(
        "/feedback",
        headers=actors["owner"],
        json={"title": "Reward history", "visibility": "private"},
    ).json()["data"]["id"]
    awarded = client.post(f"/admin/feedback/{report}/reward", headers=actors["triager"])
    assert awarded.status_code == 200, awarded.text
    response = client.get("/users/me/credits/rewards", headers=actors["owner"])
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["feedback_count"] == 1
    assert data["feedback_credits"] == 100
    assert {(item["kind"], item["credits"]) for item in data["items"]} == {
        ("beta", 500),
        ("feedback", 100),
    }
    other = client.get("/users/me/credits/rewards", headers=actors["outsider"])
    assert other.status_code == 200, other.text
    assert other.json()["data"] == {
        "feedback_count": 0,
        "feedback_credits": 0,
        "invite_credits": 0,
        "items": [],
    }


def test_members_can_read_published_plans_without_administrator_data(client, actors):
    assert client.get("/credits/plans").status_code == 401
    created = client.post(
        "/admin/plans",
        headers=actors["admin"],
        json={
            "key": "research-ui",
            "name": "Research Team",
            "audience": "team",
            "credits_per_period": 5000,
        },
    )
    assert created.status_code == 201, created.text
    response = client.get("/credits/plans", headers=actors["owner"])
    assert response.status_code == 200, response.text
    plans = response.json()["data"]["plans"]
    assert "reserve" not in {plan["key"] for plan in plans}
    research = next(plan for plan in plans if plan["key"] == "research-ui")
    assert research["credits_per_period"] == 5000
    assert research["audience"] == "team"
    assert isinstance(research["models"], list)
    assert not {"admin_only", "team_count", "is_default", "rank"} & research.keys()
