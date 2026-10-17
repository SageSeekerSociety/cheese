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


def test_the_script_as_a_process_exits_nonzero_on_drift(tmp_path: Path) -> None:
    """The point of the change: the exit status, not the printed report."""
    root = tmp_path / "backend"
    (root / "app").mkdir(parents=True)
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "main.py").write_text(
        "from fastapi import FastAPI\n"
        "\n"
        "app = FastAPI()\n"
        "\n"
        "\n"
        "@app.get('/things')\n"
        "def things() -> list[str]:\n"
        "    return []\n",
        encoding="utf-8",
    )
    script = BACKEND / "scripts" / "check_api_parity.py"
    (root / "scripts").mkdir()
    shutil.copy(script, root / "scripts" / "check_api_parity.py")
    catalog = root / "tests" / "contract" / "api_catalog.json"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(
        json.dumps(
            [
                {"method": "GET", "path": "/things"},
                {"method": "GET", "path": "/gone"},
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "scripts/check_api_parity.py"],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root)},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1, result.stdout + result.stderr
