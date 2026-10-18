"""Retired admin endpoints do not expose data or actions."""

from fastapi.testclient import TestClient

from app.main import app


def test_retired_architecture_board_routes_return_404():
    with TestClient(app) as client:
        assert client.get("/admin/ratchet").status_code == 404
        assert client.post("/admin/ratchet/refresh").status_code == 404
