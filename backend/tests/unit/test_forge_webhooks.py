"""Only the platform subscription is replaced when repairing event delivery."""

import json
import uuid
from types import SimpleNamespace

import httpx
import pytest

from app.core.config import settings
from app.domain.agent.forgejo_tokens import seal_forge_password
from app.domain.project.forge import ensure_repository_webhook


@pytest.fixture
def binding(monkeypatch):
    monkeypatch.setattr(
        settings, "forge_webhook_url", "https://relay.invalid/events/dev"
    )
    monkeypatch.setattr(settings, "forge_event_secret", "deployment-secret")
    project_id = uuid.uuid4()
    return SimpleNamespace(
        kind="forgejo",
        project_id=project_id,
        api_url="https://forge.invalid/api/v1",
        repo="owner/project",
        account_password=seal_forge_password(project_id, "account-password"),
    )


@pytest.mark.anyio
async def test_paginated_repair_preserves_unrelated_hooks(binding):
    owned_url = f"{settings.forge_webhook_url}/{binding.project_id}"
    old = {
        "id": 51,
        "type": "forgejo",
        "events": ["push"],
        "config": {"url": owned_url},
    }
    calls = []

    def respond(request):
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            page = int(request.url.params["page"])
            hooks = (
                [
                    {"id": i, "config": {"url": f"https://other.invalid/{i}"}}
                    for i in range(50)
                ]
                if page == 1
                else [old]
            )
            return httpx.Response(200, json=hooks)
        if request.method == "POST":
            payload = json.loads(request.content)
            assert "action_run_success" in payload["events"]
            return httpx.Response(201, json={"id": 52, **payload})
        assert request.method == "DELETE" and request.url.path.endswith("/51")
        return httpx.Response(204)

    await ensure_repository_webhook(binding, transport=httpx.MockTransport(respond))
    assert [method for method, _ in calls] == ["GET", "GET", "POST", "DELETE"]


@pytest.mark.anyio
async def test_failed_replacement_keeps_the_old_subscription(binding):
    methods = []

    def respond(request):
        methods.append(request.method)
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 1,
                        "type": "forgejo",
                        "events": ["push"],
                        "config": {
                            "url": f"{settings.forge_webhook_url}/{binding.project_id}"
                        },
                    }
                ],
            )
        return httpx.Response(503)

    with pytest.raises(httpx.HTTPStatusError):
        await ensure_repository_webhook(binding, transport=httpx.MockTransport(respond))
    assert methods == ["GET", "POST"]


@pytest.mark.anyio
async def test_github_binding_keeps_its_app_subscription(binding):
    binding.kind = "github_app"

    def unexpected(request):
        pytest.fail("GitHub App subscriptions must not be changed per repository")

    await ensure_repository_webhook(binding, transport=httpx.MockTransport(unexpected))
