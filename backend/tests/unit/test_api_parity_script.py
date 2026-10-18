"""Drift between the app's routes and the legacy catalog has to fail the run.

``check_api_parity.py`` prints what it found. Before this, that is all it did: it
returned ``None`` and the process exited 0, so a tree where every route had moved
looked exactly like a tree where nothing had.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI

from scripts import check_api_parity

BACKEND = Path(__file__).resolve().parents[2]


def check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    catalog: list[tuple[str, str]],
    routes: set[tuple[str, str]],
) -> int:
    catalog_path = tmp_path / "api_catalog.json"
    catalog_path.write_text(
        json.dumps([{"method": method, "path": path} for method, path in catalog]),
        encoding="utf-8",
    )
    monkeypatch.setattr(check_api_parity, "CATALOG_PATH", catalog_path)
    monkeypatch.setattr(check_api_parity, "collect_app_routes", lambda: set(routes))
    return check_api_parity.main()


def test_a_route_the_catalog_does_not_have_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (
        check(
            tmp_path,
            monkeypatch,
            catalog=[("GET", "/things")],
            routes={("GET", "/things"), ("GET", "/things/new")},
        )
        == 1
    )


def test_a_catalog_route_the_app_does_not_serve_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (
        check(
            tmp_path,
            monkeypatch,
            catalog=[("GET", "/things"), ("GET", "/things/gone")],
            routes={("GET", "/things")},
        )
        == 1
    )


def test_a_rename_that_keeps_the_path_shape_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (
        check(
            tmp_path,
            monkeypatch,
            catalog=[("GET", "/items/{item_id}")],
            routes={("GET", "/items/{item}")},
        )
        == 1
    )


def test_matching_routes_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert (
        check(
            tmp_path,
            monkeypatch,
            catalog=[("GET", "/things"), ("DELETE", "/things/{id}")],
            routes={("GET", "/things"), ("DELETE", "/things/{id}")},
        )
        == 0
    )


APP_SERVING_THINGS = (
    "from fastapi import FastAPI\n"
    "\n"
    "app = FastAPI()\n"
    "\n"
    "\n"
    "@app.get('/things')\n"
    "def things() -> list[str]:\n"
    "    return []\n"
)

#: The routes ``FastAPI()`` registers on its own, so a catalog can match a fake app.
FASTAPI_BUILTIN_ROUTES = [
    ("GET", "/openapi.json"),
    ("GET", "/docs"),
    ("GET", "/docs/oauth2-redirect"),
    ("GET", "/redoc"),
]


def run_as_process(
    root: Path, catalog: list[tuple[str, str]], app_source: str
) -> subprocess.CompletedProcess[str]:
    (root / "app").mkdir(parents=True)
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "main.py").write_text(app_source, encoding="utf-8")
    (root / "scripts").mkdir()
    shutil.copy(
        BACKEND / "scripts" / "check_api_parity.py",
        root / "scripts" / "check_api_parity.py",
    )
    catalog_path = root / "tests" / "contract" / "api_catalog.json"
    catalog_path.parent.mkdir(parents=True)
    catalog_path.write_text(
        json.dumps([{"method": method, "path": path} for method, path in catalog]),
        encoding="utf-8",
    )
    return subprocess.run(
        [sys.executable, "scripts/check_api_parity.py"],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root)},
        capture_output=True,
        text=True,
    )


def test_a_router_mounted_with_include_router_is_seen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FastAPI keeps an included router as one entry; its routes are still ours."""
    api = APIRouter()

    @api.get("/things/{item_id}")
    def read_thing(item_id: str) -> dict[str, str]:
        return {"id": item_id}

    mounted = FastAPI()
    mounted.include_router(api, prefix="/api")
    monkeypatch.setattr(check_api_parity, "app", mounted)

    assert ("GET", "/api/things/{item_id}") in check_api_parity.collect_app_routes()


def test_the_script_as_a_process_exits_nonzero_on_drift(tmp_path: Path) -> None:
    """The point of the change: the exit status, not the printed report."""
    result = run_as_process(
        tmp_path / "backend",
        [("GET", "/things"), ("GET", "/gone")],
        APP_SERVING_THINGS,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "GET /gone" in result.stdout
    assert "Missing exact matches: 1" in result.stdout


def test_the_script_as_a_process_exits_zero_when_routes_match(tmp_path: Path) -> None:
    result = run_as_process(
        tmp_path / "backend",
        [("GET", "/things"), *FASTAPI_BUILTIN_ROUTES],
        APP_SERVING_THINGS,
    )

    assert result.returncode == 0, result.stdout + result.stderr
