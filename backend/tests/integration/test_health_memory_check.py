"""Where an operator actually looks: `/health/detailed`, and what `/readyz` does.

The two halves are deliberately different. A memory backend that cannot reach
its models has to be *visible* — otherwise the whole probe buys nothing — but
it must not make the process unready, because the process can still serve every
request that has nothing to do with memory.
"""


import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.domain.memory import endpoint_probe

from .test_memory_endpoint_probe import _serve


@pytest.fixture(autouse=True)
def _fresh_verdict():
    endpoint_probe.reset_cache()
    yield
    endpoint_probe.reset_cache()


def test_the_db_backend_reports_a_skipped_memory_check(client, monkeypatch):
    monkeypatch.setattr(settings, "memory_backend", "db")

    async def refuse(*args, **kwargs):
        raise AssertionError("the db backend must not probe anything")

    monkeypatch.setattr(endpoint_probe, "probe", refuse)

    body = client.get("/health/detailed").json()

    assert body["checks"]["memory"]["status"] == "skipped"
    # A check with nothing to do does not drag the whole report down.
    assert body["status"] == "healthy"
    assert client.get("/readyz").status_code == 200


def test_a_dead_memory_endpoint_is_visible_but_does_not_gate_readiness(
    client, monkeypatch
):
    app = FastAPI()

    @app.post("/v1/{path:path}")
    async def unauthorized(path: str) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return JSONResponse({"error": {"message": "invalid api key"}}, status_code=401)

    with _serve(app) as base_url:
        monkeypatch.setattr(settings, "memory_backend", "openviking")
        monkeypatch.setattr(settings, "openviking_llm_api_base", base_url)
        monkeypatch.setattr(settings, "openviking_embedding_api_base", base_url)
        monkeypatch.setattr(settings, "openviking_llm_api_key", "sk-wrong")
        monkeypatch.setattr(settings, "openviking_embedding_api_key", "sk-wrong")

        detailed = client.get("/health/detailed").json()
        ready = client.get("/readyz")
        healthz = client.get("/healthz")

    # Visible: the report says degraded and names what went wrong and where.
    assert detailed["status"] == "degraded"
    assert detailed["checks"]["memory"]["status"] == "down"
    assert "401" in detailed["checks"]["memory"]["error"]

    # Not a gate: readiness only answers "can this process serve requests".
    assert ready.status_code == 200
    # And /healthz — the container health check, and so the deploy's rollback
    # gate — is untouched by any of it.
    assert healthz.status_code == 200
    assert healthz.json() == {"status": "ok"}
