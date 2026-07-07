"""The agent tool door (`cheese api post-note`) — auth + routing, no Claude, no DB.

Proves the sub-app that agents call: auth is the device token + screen token (no
per-agent JWT), so a call with no bearer is 401, and a bearer for an *unknown* device is
401 too; and its OpenAPI advertises ``post-note`` / ``whoami`` / ``finish-triage`` so
`cheese api` can derive the commands. The happy path (device+screen → agent → a block in
a thread) is DB/hub-backed and lives in the integration tests.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.agent.hub import DeviceHub
from app.api.routes.agent_api import build_agent_api
from app.db.session import AsyncSessionLocal

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _api() -> TestClient:
    agent_service = SimpleNamespace(last_thread_for_agent=lambda _uid: None)
    # A device service that recognises no device — so any bearer resolves to "unknown".
    device_service = SimpleNamespace(verify_token=_none)
    return TestClient(
        build_agent_api(AsyncSessionLocal, agent_service, DeviceHub(), device_service)  # type: ignore[arg-type]
    )


async def _none(_token: str) -> None:
    return None


async def test_post_note_requires_device_token() -> None:
    assert _api().post("/notes", json={"text": "x"}).status_code == 401


async def test_post_note_rejects_unknown_device() -> None:
    r = _api().post("/notes", json={"text": "x"}, headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


async def test_agent_api_openapi_exposes_operations() -> None:
    spec = _api().get("/openapi.json").json()
    op_ids = {op.get("operationId") for path in spec["paths"].values() for op in path.values()}
    assert "post-note" in op_ids  # cheese api derives `cheese api post-note` from this
    assert "whoami" in op_ids
    assert "finish-triage" in op_ids
