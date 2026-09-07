"""Creation notices use deployment values before a project exists."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.projects import router
from app.core.config import settings
from app.core.db import get_db


def test_creation_limits_follow_configuration(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    db = SimpleNamespace(scalar=AsyncMock(return_value=7))
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(settings, "max_concurrent_turns", 5)
    with TestClient(app) as client:
        response = client.get("/projects/resource-limits")
    assert response.status_code == 200
    assert response.json()["data"] == {
        "max_machines_per_team": 7,
        "max_concurrent_turns": 5,
    }


@pytest.mark.anyio
async def test_machine_listing_includes_the_enforced_quota(monkeypatch):
    from app.api.routes import machines

    project_id = uuid.uuid4()
    service = SimpleNamespace(
        list_for_project=AsyncMock(return_value=[]),
        quota_team_id=AsyncMock(return_value=1),
        quota_machines=AsyncMock(
            return_value=[
                SimpleNamespace(project_id=project_id),
                SimpleNamespace(project_id=uuid.uuid4()),
            ]
        ),
    )
    monkeypatch.setattr(machines, "_service", lambda db: service)
    monkeypatch.setattr(machines, "_require_project_access", AsyncMock())
    response = await machines.list_machines(
        project_id,
        SimpleNamespace(commit=AsyncMock(), scalar=AsyncMock(return_value=7)),
        None,
    )
    assert response["data"]["quota"] == {
        "team_id": 1,
        "used": 2,
        "limit": 7,
        "project_used": 1,
    }


@pytest.mark.anyio
async def test_default_is_fifty_and_updates_are_not_cached():
    from app.domain.machine.limits import get_machine_limit

    db = SimpleNamespace(scalar=AsyncMock(side_effect=[None, 75, 12]))
    assert await get_machine_limit(db) == 50
    assert await get_machine_limit(db) == 75
    assert await get_machine_limit(db) == 12


@pytest.mark.anyio
@pytest.mark.parametrize("value", [0, -1, True, 1.5, "50"])
async def test_invalid_limits_do_not_reach_storage(value):
    from app.domain.machine.limits import set_machine_limit

    db = SimpleNamespace(execute=AsyncMock())
    with pytest.raises(ValueError):
        await set_machine_limit(db, value)
    db.execute.assert_not_called()
