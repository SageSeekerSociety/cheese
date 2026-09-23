"""Run against an isolated Forgejo configured with a short OAuth token lifetime."""

import asyncio
import os
import secrets
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.domain.agent.forgejo_tokens import ForgejoTokens, seal_forge_password
from app.domain.project.models import ProjectForge


@pytest.mark.anyio
@pytest.mark.skipif(
    not os.environ.get("FORGEJO_EXPIRY_TEST_TOKEN_FILE"),
    reason="short-lived OAuth test server not configured",
)
async def test_provider_rejects_expired_token_without_backend_cleanup():
    base = os.environ["FORGEJO_EXPIRY_TEST_URL"].rstrip("/")
    admin = Path(os.environ["FORGEJO_EXPIRY_TEST_TOKEN_FILE"]).read_text().strip()
    username = "expiry-" + uuid.uuid4().hex[:12]
    password = secrets.token_hex(32) + "Aa1!"
    headers = {"Authorization": "token " + admin}
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            base + "/api/v1/admin/users",
            headers=headers,
            json={
                "username": username,
                "password": password,
                "email": username + "@users.invalid",
                "must_change_password": False,
            },
        )
        assert response.status_code == 201
        try:
            project_id = uuid.uuid4()
            binding = ProjectForge(
                project_id=project_id,
                kind="forgejo",
                repo=username + "/project",
                url=base + "/" + username + "/project.git",
                api_url=base + "/api/v1",
                account_password=seal_forge_password(project_id, password),
            )
            # No database/cache/sweeper participates in this test.
            token, expires = await ForgejoTokens(binding)._authorize()
            assert 0 < (expires - datetime.now(UTC)).total_seconds() <= 30
            for scheme in ("token", "Bearer"):
                response = await client.get(
                    base + "/api/v1/user",
                    headers={"Authorization": scheme + " " + token},
                )
                assert response.status_code == 200
                assert response.json()["login"] == username
                assert response.json()["is_admin"] is False
            # An access token must not be exchanged for a non-expiring PAT.
            response = await client.post(
                base + f"/api/v1/users/{username}/tokens",
                auth=(username, token),
                json={"name": "escape", "scopes": ["all"]},
            )
            assert response.status_code == 401
            await asyncio.sleep(
                max(0, (expires - datetime.now(UTC)).total_seconds()) + 2
            )
            for scheme in ("token", "Bearer"):
                response = await client.get(
                    base + "/api/v1/user",
                    headers={"Authorization": scheme + " " + token},
                )
                assert response.status_code == 401
            response = await client.get(base + "/api/v1/user", auth=(username, token))
            assert response.status_code == 401
            renewed, _ = await ForgejoTokens(binding)._authorize()
            assert renewed != token
            response = await client.get(base + "/api/v1/user", auth=(username, renewed))
            assert response.status_code == 200
        finally:
            response = await client.delete(
                base + "/api/v1/admin/users/" + username, headers=headers
            )
            assert response.status_code == 204
