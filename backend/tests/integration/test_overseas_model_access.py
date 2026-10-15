"""Claude, GPT and Gemini are only for teams a platform administrator approved.

A team nobody approved is not shown those models at all, whatever its plan, and
cannot save one as a project default or a teammate's model. An administrator
approves or withdraws a team from the credits console, on the record; a person
is approved through their personal team. The plan's tiers still apply on top.
"""

import pytest

from app.core.config import settings
from app.domain.agent import gateway_catalog
from tests.conftest import seed_user
from tests.integration.conftest import post_project, put_on_plan

ADMIN = "oa-admin"


@pytest.fixture(autouse=True)
def models(monkeypatch):
    monkeypatch.setattr(settings, "agent_model", "deepseek-flash")
    gateway_catalog.reset()
    yield
    gateway_catalog.reset()


@pytest.fixture
def admin(client, monkeypatch) -> dict:
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])
    return _auth(client, ADMIN)


def _auth(client, handle: str) -> dict:
    return {"Authorization": f"Bearer {seed_user(client, handle)}"}


def _project_on_reserve(client, owner: dict, handle: str) -> dict:
    r = client.post("/teams", json={"name": handle, "handle": handle}, headers=owner)
    assert r.status_code == 201, r.text
    team = r.json()["data"]["team"]["id"]
    r = post_project(client, json={"name": "Lab", "team_id": team}, headers=owner)
    assert r.status_code == 200, r.text
    project = r.json()["data"]

    async def run() -> None:
        async with client.test_request_factory() as session:
            await put_on_plan(session, team, "reserve")
            await session.commit()

    client.portal.call(run)
    return project


def _choices(client, pid: str, headers: dict) -> dict[str, dict]:
    r = client.get(f"/projects/{pid}/default-model", headers=headers)
    assert r.status_code == 200, r.text
    return {c["id"]: c for c in r.json()["data"]["choices"]}


def _approve(client, admin: dict, team_id: int, approved: bool):
    return client.put(
        f"/admin/teams/{team_id}/overseas-access",
        json={"approved": approved},
        headers=admin,
    )


def _teammate(client, pid: str, headers: dict) -> str:
    agents = client.get(f"/projects/{pid}/agents", headers=headers).json()["data"]
    return agents["data"][0]["id"]


def test_a_team_nobody_approved_does_not_see_claude_even_on_an_unlimited_plan(client):
    owner = _auth(client, "oa-owner")
    project = _project_on_reserve(client, owner, "oalab")
    pid = project["id"]

    choices = _choices(client, pid, owner)
    assert "sonnet" not in choices and "fable" not in choices
    assert choices["deepseek-flash"]["allowed"] is True

    r = client.put(
        f"/projects/{pid}/default-model", json={"model": "sonnet"}, headers=owner
    )
    assert r.status_code == 422, r.text
    r = client.put(
        f"/projects/{pid}/agents/{_teammate(client, pid, owner)}",
        json={"configuration": {"model": "sonnet"}},
        headers=owner,
    )
    assert r.status_code == 422, r.text


def test_an_administrator_approves_a_team_and_withdraws_it_on_the_record(client, admin):
    owner = _auth(client, "oa-owner")
    project = _project_on_reserve(client, owner, "oalab")
    pid, team = project["id"], project["team_id"]

    r = _approve(client, admin, team, True)
    assert r.status_code == 200, r.text
    assert _choices(client, pid, owner)["sonnet"]["allowed"] is True
    r = client.put(
        f"/projects/{pid}/default-model", json={"model": "sonnet"}, headers=owner
    )
    assert r.status_code == 200, r.text
    detail = client.get(f"/admin/teams/{team}", headers=admin).json()["data"]
    assert detail["overseas_approved"] is True
    [row] = [
        t
        for t in client.get(
            "/admin/teams", params={"q": "oalab"}, headers=admin
        ).json()["data"]["items"]
        if t["id"] == team
    ]
    assert row["overseas_approved"] is True

    assert _approve(client, admin, team, False).status_code == 200
    assert "sonnet" not in _choices(client, pid, owner)
    history = client.get(f"/admin/teams/{team}/history", headers=admin).json()["data"][
        "items"
    ]
    changes = [
        (h["before"], h["after"]) for h in history if h["action"] == "team.overseas"
    ]
    assert changes == [
        ({"approved": True}, {"approved": False}),
        ({"approved": False}, {"approved": True}),
    ]
    assert all(
        h["actor_handle"] == ADMIN for h in history if h["action"] == "team.overseas"
    )


def test_a_person_is_approved_through_their_personal_team(client, admin):
    owner = _auth(client, "oa-solo")
    r = post_project(client, json={"name": "Mine"}, headers=owner)
    assert r.status_code == 200, r.text
    project = r.json()["data"]
    assert "sonnet" not in _choices(client, project["id"], owner)

    assert _approve(client, admin, project["team_id"], True).status_code == 200
    # Shown now; Free still does not include its tier.
    sonnet = _choices(client, project["id"], owner)["sonnet"]
    assert sonnet["allowed"] is False


def test_only_a_platform_administrator_approves_a_team(client, admin):
    owner = _auth(client, "oa-owner")
    project = _project_on_reserve(client, owner, "oalab")

    for headers in (owner, {}):
        r = _approve(client, headers, project["team_id"], True)
        assert r.status_code in (401, 403), r.text
    assert "sonnet" not in _choices(client, project["id"], owner)
