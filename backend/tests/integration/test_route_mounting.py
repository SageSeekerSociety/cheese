"""A partially mounted app must not look healthy.

A route module that fails to import is skipped so one bad file cannot take the
whole app down. The cost is that the app then serves 404 for a whole group of
endpoints while reporting itself fine — the only party that finds out is the
caller, which is how a typo ships.
"""

import pytest
from fastapi import FastAPI

from app import main


@pytest.fixture(autouse=True)
def _clean_registry():
    main.FAILED_ROUTE_MODULES.clear()
    yield
    main.FAILED_ROUTE_MODULES.clear()


def test_readiness_fails_while_a_module_is_unmounted(client, monkeypatch):
    """The rollout waits on /readyz, so this 503 is what keeps the build off traffic."""
    from app.api.routes import health

    async def _up():
        return {"status": "up"}

    # Only the routes check is under test; the dependencies answer for themselves.
    monkeypatch.setattr(health, "_check_database", _up)
    monkeypatch.setattr(health, "_check_redis", _up)
    assert client.get("/readyz").status_code == 200

    main.FAILED_ROUTE_MODULES.append("app.api.routes.agent_control")
    response = client.get("/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["unready"] == ["routes"]
    routes = body["checks"]["routes"]
    assert routes["status"] == "down"
    assert routes["unmounted"] == ["app.api.routes.agent_control"]


def test_liveness_stays_up_while_a_module_is_unmounted(client):
    """A restart cannot bring the module back, so liveness must not ask for one."""
    main.FAILED_ROUTE_MODULES.append("app.api.routes.agent_control")

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_a_broken_module_stops_the_boot_outside_production(monkeypatch):
    """In dev and CI the failure should be impossible to miss."""
    monkeypatch.setattr(main.settings, "environment", "development")

    def _boom(name):
        raise ImportError(f"boom: {name}")

    monkeypatch.setattr(main.importlib, "import_module", _boom)

    with pytest.raises(ImportError):
        main._discover_routers(FastAPI())


def test_production_keeps_serving_and_records_the_damage(monkeypatch):
    """One bad module must not take production down — but it must be visible."""
    monkeypatch.setattr(main.settings, "environment", "production")

    def _boom(name):
        raise ImportError(f"boom: {name}")

    monkeypatch.setattr(main.importlib, "import_module", _boom)

    main._discover_routers(FastAPI())

    assert main.FAILED_ROUTE_MODULES, "a skipped module left no trace"


def test_an_import_failure_at_boot_makes_the_build_unready(client, monkeypatch):
    """End to end: a module that fails to import in production → /readyz 503."""
    from app.api.routes import health

    async def _up():
        return {"status": "up"}

    monkeypatch.setattr(health, "_check_database", _up)
    monkeypatch.setattr(health, "_check_redis", _up)
    monkeypatch.setattr(main.settings, "environment", "production")
    real_import = main.importlib.import_module

    def _one_broken(name, *args, **kwargs):
        if name == "app.api.routes.agent_control":
            raise ImportError("boom")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(main.importlib, "import_module", _one_broken)
    main._discover_routers(FastAPI())

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["checks"]["routes"]["unmounted"] == [
        "app.api.routes.agent_control"
    ]
