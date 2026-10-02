"""A team picks only the models its plan allows (#2397).

Every model in a project's picker says whether the team's plan allows it, and
for one it does not, the first plan the administrator ranks that would, by its
name; the tier itself is never the answer. Saving a model the plan does not
allow is refused, as the project default, as the default for 分身 and as a teammate's
own model.
"""

import pytest
from sqlalchemy import update

from app.core.config import settings
from app.domain.agent import gateway_catalog
from app.domain.usage.models import Plan
from tests.conftest import seed_user
from tests.integration.conftest import post_project, put_on_plan


@pytest.fixture(autouse=True)
def models(monkeypatch):
    monkeypatch.setattr(settings, "agent_model", "deepseek-flash")
    gateway_catalog.reset()
    yield
    gateway_catalog.reset()


def _auth(handle: str, client) -> dict:
    return {"Authorization": f"Bearer {seed_user(client, handle)}"}


def _shared_project(client, owner: dict, handle: str) -> dict:
    r = client.post("/teams", json={"name": handle, "handle": handle}, headers=owner)
    assert r.status_code == 201, r.text
    team = r.json()["data"]["team"]["id"]
    r = post_project(client, json={"name": "Lab", "team_id": team}, headers=owner)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _choices(client, pid: str, owner: dict) -> dict[str, dict]:
    r = client.get(f"/projects/{pid}/default-model", headers=owner)
    assert r.status_code == 200, r.text
    return {c["id"]: c for c in r.json()["data"]["choices"]}


def _put_on_plan(client, team_id: int, key: str) -> None:
    async def run() -> None:
        async with client.test_request_factory() as session:
            await put_on_plan(session, team_id, key)
            await session.commit()

    client.portal.call(run)


def test_a_free_team_sees_which_plan_a_premium_model_needs_and_cannot_save_it(
    client,
):
    owner = _auth("mp-owner", client)
    project = _shared_project(client, owner, "mplab")
    pid = project["id"]

    choices = _choices(client, pid, owner)
    assert choices["sonnet"]["allowed"] is False
    assert choices["sonnet"]["requires_plan"] == "Reserve"
    assert choices["deepseek-flash"]["allowed"] is True
    assert choices["deepseek-flash"]["requires_plan"] is None

    for field in ("model", "subagent_model"):
        r = client.put(
            f"/projects/{pid}/default-model", json={field: "sonnet"}, headers=owner
        )
        assert r.status_code == 422, r.text
    teammate = client.get(f"/projects/{pid}/agents", headers=owner).json()["data"][
        "data"
    ][0]
    r = client.put(
        f"/projects/{pid}/agents/{teammate['id']}",
        json={"configuration": {"model": "sonnet"}},
        headers=owner,
    )
    assert r.status_code == 422, r.text
    r = client.post(
        f"/projects/{pid}/agents",
        json={"display_name": "Spark", "configuration": {"model": "sonnet"}},
        headers=owner,
    )
    assert r.status_code == 422, r.text

    state = client.get(f"/projects/{pid}/default-model", headers=owner).json()["data"]
    assert state["model"] is None and state["subagent_model"] is None


def test_the_plan_named_is_the_plans_own_name(client):
    owner = _auth("mp-renamer", client)
    pid = _shared_project(client, owner, "mprename")["id"]

    async def rename() -> None:
        async with client.test_request_factory() as session:
            await session.execute(
                update(Plan).where(Plan.key == "reserve").values(name="Lab Unlimited")
            )
            await session.commit()

    client.portal.call(rename)

    assert _choices(client, pid, owner)["sonnet"]["requires_plan"] == "Lab Unlimited"


def test_a_team_whose_plan_allows_the_model_picks_and_saves_it(client):
    owner = _auth("mp-reserve", client)
    project = _shared_project(client, owner, "mpreserve")
    pid = project["id"]
    _put_on_plan(client, project["team_id"], "reserve")

    choices = _choices(client, pid, owner)
    assert choices["sonnet"]["allowed"] is True
    assert choices["sonnet"]["requires_plan"] is None
    r = client.put(
        f"/projects/{pid}/default-model",
        json={"model": "sonnet", "subagent_model": "sonnet"},
        headers=owner,
    )
    assert r.status_code == 200, r.text
    teammate = client.get(f"/projects/{pid}/agents", headers=owner).json()["data"][
        "data"
    ][0]
    r = client.put(
        f"/projects/{pid}/agents/{teammate['id']}",
        json={"configuration": {"model": "sonnet"}},
        headers=owner,
    )
    assert r.status_code == 200, r.text


def test_a_personal_team_is_not_told_of_a_plan_it_cannot_be_put_on(client):
    """Reserve is for shared teams only; a person's own team on Free is told
    the model is not available, not that it needs Reserve."""
    owner = _auth("mp-solo", client)
    r = post_project(client, json={"name": "Mine"}, headers=owner)
    assert r.status_code == 200, r.text
    pid = r.json()["data"]["id"]

    sonnet = _choices(client, pid, owner)["sonnet"]
    assert sonnet["allowed"] is False
    assert sonnet["requires_plan"] is None


def test_the_plan_named_is_the_first_the_administrator_ranks_that_allows_it(client):
    """Plans carry no price, so the administrator's order decides which plan a
    model is said to need, whatever each plan issues."""
    owner = _auth("mp-ranked", client)
    pid = _shared_project(client, owner, "mpranked")["id"]

    async def plans(pro_rank: int, team_rank: int) -> None:
        async with client.test_request_factory() as session:
            for key, name, credits, rank in (
                ("pro", "Pro", 100.0, pro_rank),
                ("team", "Team", 1000.0, team_rank),
            ):
                plan = await session.get(Plan, key)
                if plan is None:
                    plan = Plan(
                        key=key,
                        name=name,
                        audience="team",
                        credits_per_period=credits,
                        windows=[],
                        model_tiers=["included", "premium"],
                    )
                    session.add(plan)
                plan.rank = rank
            await session.commit()

    client.portal.call(plans, 20, 10)
    assert _choices(client, pid, owner)["sonnet"]["requires_plan"] == "Team"
    client.portal.call(plans, 10, 20)
    assert _choices(client, pid, owner)["sonnet"]["requires_plan"] == "Pro"
