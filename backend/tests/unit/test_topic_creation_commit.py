"""A creation response must describe a committed, immediately readable room."""

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from app.api.auth import get_actor_resolver
from app.api.routes import topics
from app.core import db
from app.domain.topic.schemas import TopicOut


@pytest.mark.anyio
@pytest.mark.parametrize("commit_fails", [False, True])
async def test_creation_response_follows_commit(monkeypatch, commit_fails):
    committed = False

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def commit(self):
            nonlocal committed
            if commit_fails:
                raise RuntimeError("commit failed")
            committed = True

        async def rollback(self):
            pass

    project_id = uuid.uuid4()
    topic = TopicOut(
        id=uuid.uuid4(),
        project_id=project_id,
        parent_id=None,
        title="test room",
        kind="topic",
        status="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    monkeypatch.setattr(db, "async_session_factory", Session)
    monkeypatch.setattr(
        topics,
        "TopicService",
        lambda session: SimpleNamespace(create=AsyncMock(return_value=topic)),
    )
    resolver = SimpleNamespace(
        resolve=AsyncMock(return_value=SimpleNamespace(handle="owner")),
        authorize_project=AsyncMock(),
    )
    app = FastAPI()
    app.dependency_overrides[get_actor_resolver] = lambda: resolver
    app.post("/topics")(topics.create_topic)
    responses = []

    async def receive():
        return {
            "type": "http.request",
            "body": json.dumps(
                {"project_id": str(project_id), "title": "test room"}
            ).encode(),
            "more_body": False,
        }

    async def send(message):
        if message["type"] == "http.response.start":
            responses.append((message["status"], committed))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/topics",
        "raw_path": b"/topics",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "server": ("local", 80),
        "client": ("local", 1),
        "root_path": "",
    }
    if commit_fails:
        with pytest.raises(RuntimeError, match="commit failed"):
            await app(scope, receive, send)
        assert responses == [(500, False)]
    else:
        await app(scope, receive, send)
        assert responses == [(200, True)]
