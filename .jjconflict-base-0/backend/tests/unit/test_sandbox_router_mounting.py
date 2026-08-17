"""The sandbox router must mount even when optional assets are missing.

Route discovery imports every module under app/api/routes and SWALLOWS import
errors, so anything that can raise at import time silently removes that module's
whole router. That is exactly how `/sandbox/hooks/{topic}` disappeared from a
deployed image once: an unrelated module-level read of `backend/sandbox/cheese`
(a path the production image did not copy) raised FileNotFoundError, and every
agent hook 404'd with no error anywhere.
"""

import importlib

from app.api.routes import sandbox as sandbox_routes


def test_sandbox_module_imports_without_the_cli_asset(monkeypatch, tmp_path):
    """Import must not depend on the CLI file existing (it is served lazily)."""
    monkeypatch.setattr(sandbox_routes, "_CLI_PATH", tmp_path / "definitely-absent")
    reloaded = importlib.reload(sandbox_routes)
    assert reloaded.router is not None


def test_hook_route_is_mounted_even_with_the_asset_missing(monkeypatch, tmp_path):
    """The hooks path — the agent event lifeline — must exist regardless."""
    monkeypatch.setattr(sandbox_routes, "_CLI_PATH", tmp_path / "definitely-absent")
    # Inspect the router itself: this FastAPI version keeps included routers
    # wrapped rather than flattening their paths onto the app.
    paths = {getattr(route, "path", None) for route in sandbox_routes.router.routes}
    assert "/sandbox/hooks/{topic_id}" in paths
    assert "/sandbox/cli/cheese" in paths


def test_cli_source_returns_none_instead_of_raising(monkeypatch, tmp_path):
    """A missing asset degrades to a 503 at request time, never an import crash."""
    monkeypatch.setattr(sandbox_routes, "_CLI_PATH", tmp_path / "definitely-absent")
    assert sandbox_routes._cheese_cli_source() is None


def test_cli_source_is_shipped_in_the_repo():
    """The real asset exists where the route expects it (and the image copies it)."""
    assert sandbox_routes._CLI_PATH.exists(), sandbox_routes._CLI_PATH
    assert "cheese" in sandbox_routes._CLI_PATH.read_text(encoding="utf-8")[:200]
