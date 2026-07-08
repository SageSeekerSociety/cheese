"""Unit tests for the convenient installer (architecture §5.5).

The installer router has no DB dependency, so it is exercised against a minimal
FastAPI app that mounts only that router plus the shared ``BaseError`` handler —
no database, no lifespan, no dual-backend comparison.
"""

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import installer
from app.core.config import settings
from app.core.errors import BaseError, base_error_handler


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.add_exception_handler(BaseError, base_error_handler)  # type: ignore[arg-type]
    app.include_router(installer.router)
    return TestClient(app)


def test_install_script_is_served_with_base_baked_in(client: TestClient) -> None:
    resp = client.get("/connector/install.sh")
    assert resp.status_code == 200
    assert "shellscript" in resp.headers["content-type"]
    body = resp.text
    # The placeholder is substituted with the connector base derived from the request
    # origin: <origin>/api/connector (the edge always mounts the backend at <origin>/api).
    assert "__CONNECTOR_BASE__" not in body
    assert 'CONNECTOR_BASE="${CHEESE_CONNECTOR_BASE:-http://testserver/api/connector}"' in body
    # It is the real installer, not a stub: it fetches per-target artifacts and
    # names the device-flow next step.
    assert "/latest/$target/cheese" in body
    assert "/latest/$target/tmux" in body
    assert "cheese link connect" in body


def test_install_script_base_follows_the_forwarded_origin(client: TestClient) -> None:
    # Behind an edge/proxy the base is reconstructed from X-Forwarded-Host/Proto, so the
    # served script works under whatever host/port the client actually reached — with no
    # configured origin. This is what decouples the code from the access method.
    body = client.get(
        "/connector/install.sh",
        headers={"X-Forwarded-Host": "cheese.example.org:9000", "X-Forwarded-Proto": "https"},
    ).text
    assert 'CONNECTOR_BASE="${CHEESE_CONNECTOR_BASE:-https://cheese.example.org:9000/api/connector}"' in body
    assert "testserver" not in body


def test_cli_base_default_empty_leaves_derivation_unchanged(client: TestClient) -> None:
    # With no override configured (the default), __CLI_BASE__ is baked empty, so install.sh
    # falls back to deriving the base from CONNECTOR_BASE — byte-for-byte the old behaviour.
    body = client.get("/connector/install.sh").text
    assert "__CLI_BASE__" not in body
    assert 'base="${CHEESE_CLI_BASE:-}"' in body
    assert 'base="${CONNECTOR_BASE%/connector}"' in body  # the fallback derivation is intact


def test_cli_base_override_applies_only_to_matching_origin(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        settings,
        "connector_base_overrides",
        {"https://cheese.ruc.edu.cn/": "https://119pve.ghg.org.cn/api"},
    )
    # A matching install origin remaps the cli's runtime (WS control-channel) base, while the
    # binary-download CONNECTOR_BASE stays on the install origin (valid cert, HTTP works).
    body = client.get(
        "/connector/install.sh",
        headers={"X-Forwarded-Host": "cheese.ruc.edu.cn", "X-Forwarded-Proto": "https"},
    ).text
    assert 'base="${CHEESE_CLI_BASE:-https://119pve.ghg.org.cn/api}"' in body
    assert 'CONNECTOR_BASE="${CHEESE_CONNECTOR_BASE:-https://cheese.ruc.edu.cn/api/connector}"' in body
    # A non-matching origin gets no override — derivation unchanged.
    other = client.get(
        "/connector/install.sh",
        headers={"X-Forwarded-Host": "other.example.com", "X-Forwarded-Proto": "https"},
    ).text
    assert 'base="${CHEESE_CLI_BASE:-}"' in other


def test_artifact_rejects_unknown_target(client: TestClient) -> None:
    resp = client.get("/connector/latest/windows-amd64/cheese")
    assert resp.status_code == 400


def test_artifact_rejects_unknown_artifact(client: TestClient) -> None:
    resp = client.get("/connector/latest/linux-amd64/rm-rf")
    assert resp.status_code == 400


def test_artifact_404_when_no_distribution_published(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "connector_dist_dir", "")
    resp = client.get("/connector/latest/linux-amd64/cheese")
    assert resp.status_code == 404


def test_artifact_is_served_when_published(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    target_dir = tmp_path / "linux-amd64"
    target_dir.mkdir()
    (target_dir / "cheese").write_bytes(b"\x7fELF-fake-binary")
    monkeypatch.setattr(settings, "connector_dist_dir", str(tmp_path))

    resp = client.get("/connector/latest/linux-amd64/cheese")
    assert resp.status_code == 200
    assert resp.content == b"\x7fELF-fake-binary"
    assert resp.headers["content-type"] == "application/octet-stream"
    assert "attachment" in resp.headers["content-disposition"]

    # A target with no published file 404s even when the dist dir exists.
    assert client.get("/connector/latest/darwin-arm64/cheese").status_code == 404


def test_served_script_matches_the_repo_file(client: TestClient) -> None:
    # Guard against the route drifting from the checked-in template.
    here = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    repo_script = os.path.join(here, "app", "agent", "install", "install.sh")
    with open(repo_script) as f:
        script = f.read()
    assert "__CONNECTOR_BASE__" in script
    assert "__CLI_BASE__" in script
