"""Creation notices use deployment values before a project exists."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.projects import router
from app.core.config import settings


def test_creation_limits_follow_configuration(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    monkeypatch.setattr(settings, "microcloud_max_machines_per_project", 7)
    monkeypatch.setattr(settings, "max_concurrent_turns", 5)
    with TestClient(app) as client:
        response = client.get("/projects/resource-limits")
    assert response.status_code == 200
    assert response.json()["data"] == {
        "max_machines_per_project": 7,
        "max_concurrent_turns": 5,
    }


@pytest.mark.anyio
async def test_machine_listing_includes_the_enforced_quota(monkeypatch):
    from app.api.routes import machines

    service = SimpleNamespace(
        list_for_project=AsyncMock(return_value=[]),
        quota_machines=AsyncMock(return_value=[object(), object()]),
    )
    monkeypatch.setattr(machines, "_service", lambda db: service)
    monkeypatch.setattr(machines, "_require_project_access", AsyncMock())
    monkeypatch.setattr(settings, "microcloud_max_machines_per_project", 7)
    response = await machines.list_machines(
        uuid.uuid4(), SimpleNamespace(commit=AsyncMock()), None
    )
    assert response["data"]["quota"] == {"used": 2, "limit": 7}
