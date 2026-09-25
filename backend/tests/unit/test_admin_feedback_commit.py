"""Admin write responses must describe state that another request can read."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.api.routes import admin_feedback
from app.api.routes.admin_common import require_platform_admin
from app.core import db


@pytest.mark.parametrize("commit_fails", [False, True])
@pytest.mark.parametrize(
    ("method", "path", "body", "service_method"),
    [
        (
            "PATCH",
            "/admin/feedback/00000000-0000-0000-0000-000000000001",
            {"assignee_handle": "alice"},
            "patch_admin",
        ),
        (
            "POST",
            "/admin/feedback/00000000-0000-0000-0000-000000000001/status",
            {"status": "in_progress"},
            "set_status",
        ),
        (
            "POST",
            "/admin/feedback/00000000-0000-0000-0000-000000000001/notes",
            {"body": "note"},
            "note",
        ),
    ],
)
async def test_admin_write_response_follows_commit(
    monkeypatch, commit_fails, method, path, body, service_method
):
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
    row = SimpleNamespace()
    service = SimpleNamespace(
        patch_admin=AsyncMock(return_value=row),
        set_status=AsyncMock(return_value=row),
        note=AsyncMock(),
        visible_row=AsyncMock(return_value=row),
    )
    monkeypatch.setattr(db, "async_session_factory", lambda: session)
    monkeypatch.setattr(
        admin_feedback.feedback_services, "FeedbackService", lambda _: service
    )
    monkeypatch.setattr(
        admin_feedback, "_detail", AsyncMock(return_value={"id": "one"})
    )

    app = FastAPI()
    app.include_router(admin_feedback.router)
    app.dependency_overrides[require_platform_admin] = lambda: "admin"
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
        response = await client.request(method, path, json=body)

    getattr(service, service_method).assert_awaited_once()
    if commit_fails:
        assert response.status_code == 500
        assert response_started == [(500, False)]
        assert session.rolled_back
    else:
        assert response.status_code == 200
        assert response_started == [(200, True)]
