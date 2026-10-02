"""Credit plans as an administrator meets them (#2397): every team starts on
Free, only a platform administrator moves a team between plans or issues it
credits, and every such change is on the record."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from tests.conftest import seed_user
from tests.integration.conftest import post_project

ADMIN = "cp-admin"


def _auth(client, handle: str) -> dict:
    return {"Authorization": f"Bearer {seed_user(client, handle)}"}


@pytest.fixture
def admin(client, monkeypatch) -> dict:
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])
    return _auth(client, ADMIN)


def _teams(client, admin: dict, q: str) -> list[dict]:
    r = client.get("/admin/teams", params={"q": q}, headers=admin)
    assert r.status_code == 200, r.text
    return r.json()["data"]["items"]


def _shared_team(client, owner: dict, handle: str) -> int:
    r = client.post("/teams", json={"name": handle, "handle": handle}, headers=owner)
    assert r.status_code == 201, r.text
    return r.json()["data"]["team"]["id"]


def test_new_personal_and_shared_teams_start_on_free(client, admin):
    owner = _auth(client, "cp-owner")
    project = post_project(client, json={"name": "P"}, headers=owner).json()["data"]
    shared = _shared_team(client, owner, "cplab")

    [personal] = [t for t in _teams(client, admin, "cp-owner") if t["personal_owner"]]
    [lab] = _teams(client, admin, "cplab")

    assert personal["id"] == project["team_id"]
    assert personal["plan_key"] == "free"
    assert lab["id"] == shared and lab["plan_key"] == "free"


def test_only_a_platform_administrator_changes_plans_and_issues_credits(client, admin):
    owner = _auth(client, "cp-owner")
    team = _shared_team(client, owner, "cplab")

    for headers in (owner, {}):
        assert client.get("/admin/plans", headers=headers).status_code in (401, 403)
        assert client.put(
            f"/admin/teams/{team}/plan", json={"plan_key": "reserve"}, headers=headers
        ).status_code in (401, 403)
        assert client.post(
            f"/admin/teams/{team}/grants", json={"credits": 10}, headers=headers
        ).status_code in (401, 403)
        assert client.put(
            "/admin/plans/free", json={"credits_per_period": 1}, headers=headers
        ).status_code in (401, 403)
    [lab] = _teams(client, admin, "cplab")
    assert lab["plan_key"] == "free" and lab["packs"] == []

    r = client.put(
        f"/admin/teams/{team}/plan", json={"plan_key": "reserve"}, headers=admin
    )
    assert r.status_code == 200, r.text
    assert _teams(client, admin, "cplab")[0]["plan_key"] == "reserve"

    audit = client.get("/admin/credits/audit", headers=admin).json()["data"]["items"]
    assert [(a["actor_handle"], a["action"], a["target"]) for a in audit] == [
        (ADMIN, "team.plan", str(team))
    ]
    assert audit[0]["before"] == {"plan_key": "free"}
    assert audit[0]["after"] == {"plan_key": "reserve"}


def test_a_team_cannot_be_put_on_a_plan_that_does_not_exist(client, admin):
    team = _shared_team(client, _auth(client, "cp-owner"), "cplab")
    r = client.put(
        f"/admin/teams/{team}/plan", json={"plan_key": "gold"}, headers=admin
    )
    assert r.status_code == 404
    assert _teams(client, admin, "cplab")[0]["plan_key"] == "free"


def test_credits_an_administrator_issues_are_the_teams_to_spend(client, admin):
    owner = _auth(client, "cp-owner")
    team = _shared_team(client, owner, "cplab")
    project = post_project(
        client, json={"name": "P", "team_id": team}, headers=owner
    ).json()["data"]
    lapses = (datetime.now(UTC) + timedelta(days=30)).isoformat()

    r = client.post(
        f"/admin/teams/{team}/grants",
        json={"credits": 40, "expires_at": lapses},
        headers=admin,
    )
    assert r.status_code == 201, r.text

    credits = client.get(f"/projects/{project['id']}/credits", headers=owner).json()[
        "data"
    ]
    assert credits["unlimited"] is False
    assert credits["credits_remaining"] == 40
    [lab] = _teams(client, admin, "cplab")
    assert [(p["source"], p["credits_total"]) for p in lab["packs"]] == [
        ("admin_grant", 40)
    ]
    audit = client.get("/admin/credits/audit", headers=admin).json()["data"]["items"]
    assert [(a["action"], a["target"]) for a in audit] == [("team.grant", str(team))]


def test_issued_credits_must_be_positive_and_lapse_in_the_future(client, admin):
    team = _shared_team(client, _auth(client, "cp-owner"), "cplab")
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    for body in ({"credits": 0}, {"credits": 5, "expires_at": past}):
        r = client.post(f"/admin/teams/{team}/grants", json=body, headers=admin)
        assert r.status_code in (400, 422), body
    assert _teams(client, admin, "cplab")[0]["packs"] == []


def test_an_administrator_edits_what_a_plan_issues(client, admin):
    r = client.put(
        "/admin/plans/free",
        json={
            "credits_per_period": 80,
            "windows": [{"hours": 5, "credits": 20}],
        },
        headers=admin,
    )
    assert r.status_code == 200, r.text
    plans = {
        p["key"]: p
        for p in client.get("/admin/plans", headers=admin).json()["data"]["plans"]
    }
    assert plans["free"]["credits_per_period"] == 80
    assert plans["free"]["windows"] == [{"hours": 5, "credits": 20}]
    assert plans["reserve"]["unlimited"] is True

    r = client.put("/admin/plans/free", json={"model_tiers": ["gold"]}, headers=admin)
    assert r.status_code == 400


def test_the_migration_puts_every_existing_team_on_free(client):
    """Run against a scratch schema holding teams from before plans existed."""
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/a6d3f1c9e842_credit_plans.py"
    )
    spec = importlib.util.spec_from_file_location("credit_plans_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    # A deployment builds the index outside a transaction; the scratch schema
    # lives inside one that is rolled back.
    migration.op = _InOneTransaction(migration.op)  # type: ignore[attr-defined]

    def verify(connection):
        for statement in (
            "CREATE SCHEMA credit_plans_migration_check",
            "SET LOCAL search_path TO credit_plans_migration_check",
            "CREATE TABLE team (id bigint PRIMARY KEY, personal_owner_user_id int)",
            "CREATE TABLE resource_usage (id uuid PRIMARY KEY, created_at timestamptz)",
            "CREATE TABLE compute_grants (id uuid PRIMARY KEY)",
            "INSERT INTO team VALUES (1, NULL), (2, 7)",
        ):
            connection.execute(text(statement))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        plans = dict(
            connection.execute(text("SELECT key, credits_per_period FROM plans")).all()
        )
        assert set(plans) == {"free", "reserve"}
        assert plans["free"] > 0
        teams = connection.execute(text("SELECT plan_key FROM team")).scalars()
        assert set(teams) == {"free"}

    async def run() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            connection = await session.connection()
            await connection.run_sync(verify)
            await session.rollback()

    asyncio.run(run())


class _InOneTransaction:
    def __init__(self, op) -> None:
        self._op = op

    def __getattr__(self, name):
        return getattr(self._op, name)

    def get_context(self):
        from contextlib import nullcontext

        class _Context:
            def autocommit_block(self):
                return nullcontext()

        return _Context()

    def create_index(self, *args, **kwargs):
        kwargs.pop("postgresql_concurrently", None)
        return self._op.create_index(*args, **kwargs)


@pytest.mark.anyio
async def test_a_key_left_with_nothing_to_cap_it_loses_its_budget(
    business_db_factory, tmp_path, monkeypatch
):
    """When a project's spending is no longer capped by any pack, a budget
    left on its gateway key would refuse calls the platform now admits."""
    from app.domain.agent.chat import ChatService
    from app.domain.project.services import ProjectService
    from app.domain.usage.ledger import Ledger
    from tests.conftest import stub_compute
    from tests.integration.conftest import registered
    from tests.integration.test_gateway_usage import FakeGateway

    monkeypatch.setattr(settings, "llm_gateway_credit_usd", 0.01)
    fake = FakeGateway()
    svc = ChatService(
        session_factory=business_db_factory,
        compute=stub_compute(),
        base_system_prompt="",
        workspace_root=str(tmp_path / "ws"),
        gateway=fake,  # type: ignore[arg-type]
    )
    async with business_db_factory() as session:
        await registered(session, "u")
        project = await ProjectService(session).create(name="P", owner_handle="u")
        pack = await Ledger(session).grant(project.team_id, 100)
        pid, pack_id = project.id, pack.id
        await session.commit()

    key = await svc.project_gateway_key(pid)
    assert fake.budgets == [(key, 1.0)]

    async with business_db_factory() as session:
        from app.domain.usage.models import ComputeGrant

        lapsed = await session.get(ComputeGrant, pack_id)
        assert lapsed is not None
        lapsed.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    await svc.project_gateway_key(pid)
    assert fake.budgets[-1] == (key, None)


def test_an_administrator_creates_a_plan_and_puts_a_matching_team_on_it(client, admin):
    r = client.post(
        "/admin/plans",
        json={
            "key": "school",
            "name": "School",
            "audience": "team",
            "credits_per_period": 500,
            "windows": [{"hours": 168, "credits": 200}],
            "model_tiers": ["included", "premium"],
        },
        headers=admin,
    )
    assert r.status_code == 201, r.text
    owner = _auth(client, "cp-owner")
    lab = _shared_team(client, owner, "cplab")
    personal = post_project(client, json={"name": "P"}, headers=owner).json()["data"][
        "team_id"
    ]

    # A plan for shared teams is not a personal team's to be on.
    refused = client.put(
        f"/admin/teams/{personal}/plan", json={"plan_key": "school"}, headers=admin
    )
    assert refused.status_code == 400
    r = client.put(
        f"/admin/teams/{lab}/plan", json={"plan_key": "school"}, headers=admin
    )
    assert r.status_code == 200, r.text

    detail = client.get(f"/admin/teams/{lab}", headers=admin).json()["data"]
    assert detail["plan"]["key"] == "school"
    assert detail["plan"]["credits_per_period"] == 500
    assert detail["plan"]["model_tiers"] == ["included", "premium"]


def test_a_teams_history_lists_what_administrators_did_to_it_newest_first(
    client, admin
):
    owner = _auth(client, "cp-owner")
    lab = _shared_team(client, owner, "cplab")
    other = _shared_team(client, owner, "cpother")
    client.post(
        f"/admin/teams/{lab}/grants",
        json={"credits": 30, "reason": "合同 2026-17"},
        headers=admin,
    )
    client.put(f"/admin/teams/{lab}/plan", json={"plan_key": "reserve"}, headers=admin)
    client.post(f"/admin/teams/{other}/grants", json={"credits": 5}, headers=admin)

    history = client.get(f"/admin/teams/{lab}/history", headers=admin).json()["data"][
        "items"
    ]
    assert [h["action"] for h in history] == ["team.plan", "team.grant"]
    assert history[1]["after"]["reason"] == "合同 2026-17"

    [pack] = client.get(f"/admin/teams/{lab}", headers=admin).json()["data"]["packs"]
    assert (pack["source"], pack["credits_total"], pack["reason"]) == (
        "admin_grant",
        30,
        "合同 2026-17",
    )


def test_a_teams_earmarked_credits_name_their_task_and_project(client, admin):
    from tests.conftest import seed_task_with_protocol

    owner = _auth(client, "cp-owner")
    task_id = seed_task_with_protocol(client, resource_pack={"compute_credits": 300})

    async def earmark() -> tuple[int, str]:
        from app.domain.project.services import ProjectService
        from app.domain.usage.ledger import Ledger
        from tests.integration.conftest import registered

        async with client.test_factory() as session:  # type: ignore[attr-defined]
            await registered(session, "cp-owner")
            project = await ProjectService(session).create(
                name="赛题项目", owner_handle="cp-owner"
            )
            await Ledger(session).grant_earmark(
                project_id=project.id, source_task_id=task_id, credits_total=300
            )
            await session.commit()
            return project.team_id, project.name

    team, name = asyncio.run(earmark())
    del owner
    [pack] = client.get(f"/admin/teams/{team}", headers=admin).json()["data"]["packs"]
    assert pack["source"] == "task_earmark"
    assert pack["project_name"] == name
    assert pack["task_id"] == task_id and pack["task_name"]
