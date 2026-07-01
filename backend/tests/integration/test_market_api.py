"""市场 catalog + per-project compute pool selection."""


def _project(client) -> str:
    return client.post("/api/projects", json={"name": "P"}).json()["data"]["id"]


def test_market_lists_ai_and_compute_pools(client):
    data = client.get("/api/market/pools").json()["data"]
    # Both axes are present.
    assert {p["kind"] for p in data["ai"]} == {"ai"}
    assert {p["kind"] for p in data["compute"]} == {"compute"}
    # The default AI pool and local compute are available; each listing carries a
    # price + description for the browse view.
    ai_default = next(p for p in data["ai"] if p["default"])
    assert ai_default["available"] and ai_default["price"] and ai_default["description"]
    local = next(p for p in data["compute"] if p["id"] == "local-docker")
    assert local["available"] and local["default"]
    # Undeployed pools show in the catalog but are marked unavailable.
    gpu = next(p for p in data["compute"] if p["id"] == "gpu")
    assert gpu["available"] is False


def test_compute_profiles_default_and_reject_undeployed(client):
    pid = _project(client)
    body = client.get(f"/api/projects/{pid}/compute-profiles").json()["data"]
    assert body["current"] == "local-docker"
    assert [p["id"] for p in body["profiles"]] == ["local-docker"]

    # Selecting a pool that isn't deployed is rejected (no silent fallback).
    r = client.put(f"/api/projects/{pid}/compute-profile", json={"profile": "gpu"})
    assert r.status_code == 422

    # Selecting the deployed local pool persists.
    r = client.put(
        f"/api/projects/{pid}/compute-profile", json={"profile": "local-docker"}
    )
    assert r.status_code == 200
    cur = client.get(f"/api/projects/{pid}/compute-profiles").json()["data"]["current"]
    assert cur == "local-docker"
