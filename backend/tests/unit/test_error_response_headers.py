"""Headers attached to an HTTPException have to reach the client.

FastAPI's own handler forwards `exc.headers`. This codebase replaces that
handler, and the replacement dropped them — so the one place that uses them, the
connection owner naming WHICH device went offline, never got its answer across.
The client half is pinned in `test_owner_rpc_unexpected_409.py`; this is the
half that puts the header on the wire.
"""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.errors import register_exception_handlers


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/offline")
    async def _offline() -> dict:
        # Exactly what device_connection_app raises for DeviceOffline.
        raise HTTPException(
            status_code=409,
            detail="device offline",
            headers={"X-Device-Id": "machine-7"},
        )

    @app.get("/plain")
    async def _plain() -> dict:
        raise HTTPException(status_code=404, detail="nothing here")

    return TestClient(app, raise_server_exceptions=False)


def test_a_device_offline_409_says_which_device(client: TestClient) -> None:
    response = client.get("/offline")
    assert response.status_code == 409
    assert response.headers.get("X-Device-Id") == "machine-7"


def test_the_body_is_still_the_envelope_every_other_error_uses(
    client: TestClient,
) -> None:
    """Forwarding headers must not change the shape clients parse."""
    response = client.get("/offline")
    assert response.json() == {
        "code": 409,
        "message": "Error: device offline",
        "error": {"name": "Error", "message": "device offline", "data": None},
    }


def test_an_error_raised_without_headers_still_works(client: TestClient) -> None:
    response = client.get("/plain")
    assert response.status_code == 404
    assert response.json()["error"]["message"] == "nothing here"
    assert "X-Device-Id" not in response.headers


def test_an_event_stream_error_carries_them_too(client: TestClient) -> None:
    """The SSE branch is a second return path and was missing them as well."""
    response = client.get("/offline", headers={"accept": "text/event-stream"})
    assert response.status_code == 409
    assert response.headers.get("X-Device-Id") == "machine-7"
    assert "event: error" in response.text
