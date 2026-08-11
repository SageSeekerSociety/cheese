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


def test_healthz_reports_a_module_that_did_not_mount(client):
    assert client.get("/healthz").json()["status"] == "ok"

    main.FAILED_ROUTE_MODULES.append("app.api.routes.terminal")
    body = client.get("/healthz").json()

    assert body["status"] == "degraded"
    assert "app.api.routes.terminal" in body["unmounted"]


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
