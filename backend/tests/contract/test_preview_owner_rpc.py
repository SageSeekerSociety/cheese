"""RPC1 authentication, bounded reads and unavailable-owner behavior.

These cases cross the real owner parser/auth/serializer without substituting
the owner route. No DB/content authorization or deployment claim is made here.
"""

import asyncio
import time
import uuid

import httpx
import jwt
import pytest

from app.core.config import settings
from app.domain.agent import preview_owner
from app.domain.agent.preview_owner import (
    AUDIENCE,
    INSPECT_PATH,
    PreviewOwnerClient,
    service_token,
)
from app.preview_connection_app import create_app


def _run(test):
    return asyncio.run(test())


@pytest.mark.parametrize(
    "credential", ["missing", "wrong_audience", "expired", "user_key"]
)
def test_owner_refuses_untrusted_reads_before_processing_body(credential):
    async def scenario():
        now = int(time.time())
        claims = {
            "aud": AUDIENCE,
            "sub": "preview-backend",
            "iat": now,
            "exp": now + 30,
        }
        key = settings.preview_connection_auth_secret
        if credential == "wrong_audience":
            claims["aud"] = "preview-session"
        if credential == "expired":
            claims.update(iat=now - 60, exp=now - 30)
        if credential == "user_key":
            key = settings.jwt_secret
        headers = (
            {}
            if credential == "missing"
            else {
                "Authorization": "Bearer " + jwt.encode(claims, key, algorithm="HS256")
            }
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://owner"
        ) as client:
            response = await client.post(
                INSPECT_PATH, content=b"x" * 2000, headers=headers
            )
        assert response.status_code == 403

    _run(scenario)


@pytest.mark.parametrize(
    "body,status",
    [
        (b"x" * 1025, 413),
        (b'{"topic_id":"bad","seat":"seat"}', 422),
        (
            b'{"topic_id":"00000000-0000-0000-0000-000000000000","seat":"seat","wait_ms":8001}',
            422,
        ),
        (
            b'{"topic_id":"00000000-0000-0000-0000-000000000000","seat":"seat","url":"http://internal/"}',
            422,
        ),
    ],
)
def test_owner_bounds_and_restricts_the_read(body, status):
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://owner"
        ) as client:
            response = await client.post(
                INSPECT_PATH,
                content=body,
                headers={"Authorization": "Bearer " + service_token()},
            )
        assert response.status_code == status

    _run(scenario)


def test_backend_reads_real_owner_and_never_uses_its_local_hub(monkeypatch):
    monkeypatch.setattr(settings, "preview_connection_url", "http://owner")

    async def scenario():
        application = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application)
        ) as http:
            result = await PreviewOwnerClient(http).inspect(uuid.uuid4(), "seat")
        assert result.state == "transport_unavailable"
        assert result.owner_incarnation == application.state.preview_incarnation
        monkeypatch.setattr(preview_owner, "_client", None)
        assert (
            await preview_owner.inspect_owner(uuid.uuid4(), "seat")
        ).state == "transport_unavailable"

    _run(scenario)


def test_owner_mode_does_not_retry_or_follow_an_rpc_redirect(monkeypatch):
    monkeypatch.setattr(settings, "preview_connection_url", "http://owner")
    requests = []

    async def answer(request):
        requests.append(request)
        return httpx.Response(307, headers={"Location": "http://elsewhere/"})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
            result = await PreviewOwnerClient(http).inspect(uuid.uuid4(), "seat")
        assert result.state == "transport_unavailable"
        assert len(requests) == 1
        assert requests[0].url.path == INSPECT_PATH

    _run(scenario)


def test_owner_bounds_pending_reads_and_releases_cancelled_callers():
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://owner"
        ) as client:
            body = {"topic_id": str(uuid.uuid4()), "seat": "seat", "wait_ms": 8000}
            headers = {"Authorization": "Bearer " + service_token()}
            pending = [
                asyncio.create_task(
                    client.post(
                        INSPECT_PATH,
                        json=body,
                        headers=headers,
                    )
                )
                for _ in range(64)
            ]
            try:
                await asyncio.sleep(0)
                refused = await client.post(INSPECT_PATH, json=body, headers=headers)
                assert refused.status_code == 503
                assert refused.headers["Retry-After"] == "1"
            finally:
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
            body["wait_ms"] = 0
            fresh = await client.post(INSPECT_PATH, json=body, headers=headers)
            assert fresh.status_code == 200
            assert fresh.json()["state"] == "transport_unavailable"

    _run(scenario)


@pytest.mark.parametrize(
    "payload",
    [
        {"protocol": 2, "owner_incarnation": "other", "state": "online"},
        {
            "protocol": 1,
            "owner_incarnation": "other",
            "state": "online",
            "instance": "b" * 64,
            "transport_epoch": "e",
        },
    ],
)
def test_mixed_version_or_different_instance_cannot_authorize_a_selection(
    monkeypatch, payload
):
    monkeypatch.setattr(settings, "preview_connection_url", "http://owner")

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
        ) as http:
            result = await PreviewOwnerClient(http).inspect(
                uuid.uuid4(), "seat", expected_instance="a" * 64
            )
        assert result.state == "transport_unavailable"
        assert result.instance is None

    _run(scenario)
