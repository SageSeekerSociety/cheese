"""A successful project response must describe committed, usable state."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.api.auth import get_actor_resolver
from app.api.routes import projects
from app.core import db


@pytest.mark.parametrize("commit_fails", [False, True])
async def test_project_response_waits_for_commit(monkeypatch, commit_fails):
    class Session:
        committed = False
        rolled_back = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def commit(self):
            if commit_fails:
                raise RuntimeError("commit failed")
            self.committed = True

        async def rollback(self):
            self.rolled_back = True

    session = Session()
    project = SimpleNamespace(
        id=uuid.uuid4(),
        name="project",
        owner_handle="owner",
        team_id=1,
        external_task_id=None,
        ai_mode="collaborative",
        summary="",
        root_topic_id=uuid.uuid4(),
        created_at=datetime.now(UTC),
    )
    monkeypatch.setattr(db, "async_session_factory", lambda: session)
    monkeypatch.setattr(
        projects,
        "ProjectService",
        lambda _: SimpleNamespace(create=AsyncMock(return_value=project)),
    )
    app = FastAPI()
    app.include_router(projects.router)
    app.dependency_overrides[get_actor_resolver] = lambda: SimpleNamespace(
        resolve=AsyncMock(
            return_value=SimpleNamespace(handle="owner", authenticated=True)
        )
    )
    response_started = []

    async def observe(scope, receive, send):
        async def record(message):
            if message["type"] == "http.response.start":
                response_started.append((message["status"], session.committed))
            await send(message)

        await app(scope, receive, record)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=observe, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post("/projects", json={"name": "project"})
    if commit_fails:
        assert response.status_code == 500
        assert response_started == [(500, False)]
        assert session.rolled_back
    else:
        assert response.status_code == 200
        assert response_started == [(200, True)]
