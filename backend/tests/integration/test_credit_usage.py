"""What members see of their credits (#2397, #2233): a team's members read its
month, nobody else does; a person's own page splits their spend by product
line; figures are in credits (点), never tokens or a person's share of a
team's projects (#394)."""

import asyncio
import json
import uuid

import jwt
import pytest

from app.domain.project.repositories import ProjectRepository
from app.domain.usage.ledger import Ledger, payer_for_person, payer_for_project
from tests.conftest import seed_user
from tests.integration.conftest import free_plan_credits, post_project


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _user_id(token: str) -> int:
    return int(jwt.decode(token, options={"verify_signature": False})["sub"])


def _shared_team(client, owner: dict, handle: str) -> int:
    r = client.post("/teams", json={"name": handle, "handle": handle}, headers=owner)
    assert r.status_code == 201, r.text
    return r.json()["data"]["team"]["id"]


def _spend(
    client,
    *,
    credits: float,
    kind: str,
    project: str | None = None,
    user_id: int | None = None,
) -> None:
    """Record one charged call, inside ``project`` or for ``user_id`` outside
    any project."""

    async def run() -> None:
        async with client.test_factory() as session:
            if project is not None:
                payer = await payer_for_project(session, uuid.UUID(project))
            else:
                assert user_id is not None
                payer = await payer_for_person(session, user_id)
            await Ledger(session).record(
                payer,
                credits=credits,
                model="m",
                input_tokens=10,
                output_tokens=10,
                cost_usd=0.01,
                route="gateway",
                kind=kind,
                user_id=user_id if project is None else None,
            )
            await session.commit()

    asyncio.run(run())


def _team_of(client, project_id: str) -> int:
    async def run() -> int:
        async with client.test_factory() as session:
            team = await ProjectRepository(session).team_for_project(
                uuid.UUID(project_id)
            )
            assert team is not None
            return team

    return asyncio.run(run())


@pytest.fixture
def plan(client):
    free_plan_credits(client, 100)


def test_members_read_their_teams_usage_and_others_cannot(client, plan):
    owner_token = seed_user(client, "cu-owner")
    owner = _auth(owner_token)
    outsider = _auth(seed_user(client, "cu-outsider"))
    team = _shared_team(client, owner, "culab")
    project = post_project(
        client, json={"name": "Board", "team_id": team}, headers=owner
    ).json()["data"]
    _spend(client, credits=25, kind="chat", project=project["id"])

    r = client.get(f"/teams/{team}/credits/usage", headers=owner)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["plan"]["key"] == "free"
    assert data["plan"]["credits_per_period"] == 100
    assert data["period"]["credits_used"] == 25
    assert data["period"]["credits_total"] == 100
    assert data["period"]["used_ratio"] == pytest.approx(0.25)
    assert data["period"]["remaining_ratio"] == pytest.approx(0.75)
    assert data["period"]["resets_at"]

    assert client.get(f"/teams/{team}/credits/usage", headers=outsider).status_code in (
        403,
        404,
    )
    assert client.get(f"/teams/{team}/credits/usage").status_code == 401


def test_a_persons_page_splits_their_spend_by_product_line(client, plan):
    token = seed_user(client, "cu-person")
    me = _auth(token)
    user_id = _user_id(token)
    own_project = post_project(client, json={"name": "Mine"}, headers=me).json()["data"]
    _spend(client, credits=10, kind="chat", project=own_project["id"])
    _spend(client, credits=5, kind="assistant", user_id=user_id)
    _spend(client, credits=3, kind="docs_ask", user_id=user_id)
    _spend(client, credits=2, kind="task_pdf_draft", user_id=user_id)

    data = client.get("/users/me/credits/usage", headers=me).json()["data"]

    assert data["lines"] == {
        "collab": pytest.approx(10),
        "ask": pytest.approx(8),
        "write": pytest.approx(2),
        "compute": 0,
    }
    assert data["period"]["credits_used"] == pytest.approx(20)
    [project] = data["projects"]
    assert project["name"] == "Mine" and project["credits"] == pytest.approx(10)


def test_project_spend_adds_up_to_the_teams_month(client, plan):
    owner = _auth(seed_user(client, "cu-owner"))
    team = _shared_team(client, owner, "culab")
    ids = [
        post_project(client, json={"name": n, "team_id": team}, headers=owner).json()[
            "data"
        ]["id"]
        for n in ("A", "B", "C")
    ]
    for project_id, credits in zip(ids, (12, 6, 2), strict=True):
        _spend(client, credits=credits, kind="chat", project=project_id)

    data = client.get(f"/teams/{team}/credits/usage", headers=owner).json()["data"]

    spent = {p["name"]: p["credits"] for p in data["projects"]}
    assert spent == {
        "A": pytest.approx(12),
        "B": pytest.approx(6),
        "C": pytest.approx(2),
    }
    days = [d["credits"] for d in data["days"] if d["credits"] is not None]
    assert sum(days) == pytest.approx(data["period"]["credits_used"]) == 20


def test_no_figure_is_a_persons_share_of_a_teams_projects(client, plan):
    owner_token = seed_user(client, "cu-owner")
    owner = _auth(owner_token)
    team = _shared_team(client, owner, "culab")
    project = post_project(
        client, json={"name": "Board", "team_id": team}, headers=owner
    ).json()["data"]
    assert _team_of(client, project["id"]) == team
    _spend(client, credits=40, kind="chat", project=project["id"])

    team_view = client.get(f"/teams/{team}/credits/usage", headers=owner).json()
    mine = client.get("/users/me/credits/usage", headers=owner).json()["data"]

    # Spend in a shared team's project is the team's, not the person's.
    assert mine["period"]["used_ratio"] == 0
    assert mine["projects"] == []
    assert mine["lines"] == {"collab": 0, "ask": 0, "write": 0, "compute": 0}
    [lab] = mine["teams"]
    assert lab["id"] == team and lab["credits_remaining"] == pytest.approx(60)
    # No tokens, costs or person ids anywhere.
    for body in (json.dumps(team_view), json.dumps(mine)):
        for word in ("tokens", "user_id", "cost"):
            assert word not in body, word


def test_a_windowed_teams_page_shows_each_window_and_when_it_resets(client):
    """A plan that issues no pack is not unlimited: its page reads how full
    each window is, and the member's list of teams reads its fullest one."""
    from sqlalchemy import update

    from app.domain.usage.models import Plan

    async def windowed() -> None:
        async with client.test_factory() as session:
            await session.execute(
                update(Plan)
                .where(Plan.key == "free")
                .values(
                    credits_per_period=None,
                    windows=[
                        {"hours": 5, "credits": 10},
                        {"calendar": "month", "credits": 100},
                    ],
                )
            )
            await session.commit()

    asyncio.run(windowed())
    owner = _auth(seed_user(client, "cu-window"))
    team = _shared_team(client, owner, "cuwindow")
    project = post_project(
        client, json={"name": "Board", "team_id": team}, headers=owner
    ).json()["data"]

    data = client.get(f"/teams/{team}/credits/usage", headers=owner).json()["data"]
    assert data["period"] is None
    hours, month = data["windows"]
    assert hours["used_ratio"] == 0 and hours["resets_at"] is None
    assert month["used_ratio"] == 0 and month["resets_at"]

    _spend(client, credits=4, kind="chat", project=project["id"])
    data = client.get(f"/teams/{team}/credits/usage", headers=owner).json()["data"]
    hours, month = data["windows"]
    assert hours["used_ratio"] == pytest.approx(0.4) and hours["resets_at"]
    assert month["used_ratio"] == pytest.approx(0.04)

    mine = client.get("/users/me/credits/usage", headers=owner).json()["data"]
    [lab] = [t for t in mine["teams"] if t["id"] == team]
    assert lab["unlimited"] is False
    assert lab["remaining_ratio"] == pytest.approx(0.6)
