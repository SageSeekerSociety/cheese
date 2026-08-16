"""市场 catalog + per-project compute pool selection."""


def _project(client) -> str:
    return client.post("/api/projects", json={"name": "P"}).json()["data"]["id"]


def test_market_lists_ai_and_compute_pools(client):
    data = client.get("/api/market/pools").json()["data"]
    # Both axes are present.
    assert {p["kind"] for p in data["ai"]} == {"ai"}
    assert {p["kind"] for p in data["compute"]} == {"compute"}
    ai_default = next(p for p in data["ai"] if p["default"])
    assert ai_default["available"] and ai_default["price"] and ai_default["description"]
    # The catalog carries only pools that exist. `local-docker` is retired (#358);
    # `remote-cheesed` and `gpu` never had a provider, a resolution path, or a
    # registration in the ComputePool at all. A permanently greyed row teaches the
    # reader that connecting something would light it up — for all three that was
    # false, so none of them is listed.
    ids = {p["id"] for p in data["compute"]}
    assert ids == {"device", "cloud"}
    # Cloud is where a topic lands when nothing was selected anywhere.
    cloud = next(p for p in data["compute"] if p["id"] == "cloud")
    assert cloud["default"] is True
    # Every listing still carries the browse-view fields.
    assert all(p["price"] and p["description"] for p in data["compute"])


def test_market_surfaces_the_whole_machine_visibility_choice_with_its_warning(client):
    """#282 §四 / #358: visibility is a catalog choice the platform SPEAKS, not a
    silent behaviour. The boxed `isolated` 档 is the conservative default but not
    deployed yet; whole-machine `host` works today but is 申请制 (non-default) and
    carries the honest #282 line as its description — so a picker reads the warning
    straight from the catalog rather than the platform granting it silently."""
    data = client.get("/api/market/pools").json()["data"]
    vis = {v["id"]: v for v in data["visibility"]}
    assert {v["kind"] for v in data["visibility"]} == {"visibility"}

    assert vis["isolated"]["default"] is True
    assert vis["isolated"]["available"] is False  # transport is #358 step 2

    assert vis["host"]["default"] is False  # whole-machine is never a default
    assert vis["host"]["available"] is True
    # The exact honest UI line #282 drafted, so the badge/tooltip copy is one source.
    assert "整台机器" in vis["host"]["description"]
    assert "其他房间" in vis["host"]["description"]


def test_compute_profiles_default_and_reject_undeployed(client):
    pid = _project(client)
    body = client.get(f"/api/projects/{pid}/compute-profiles").json()["data"]
    # Nothing selected anywhere → Cloud, the fallback (#358 retired local-docker,
    # which used to be this answer).
    assert body["current"] == "cloud"
    # With no device online and MicroCloud unconfigured in this test, nothing is
    # actually deployed, so there is nothing to offer.
    assert [p["id"] for p in body["profiles"]] == []

    # Selecting a pool that isn't deployed is rejected — no silent fallback. That
    # is the property this test exists for; the retired pool is a natural sample.
    r = client.put(
        f"/api/projects/{pid}/compute-profile", json={"profile": "local-docker"}
    )
    assert r.status_code == 422

    # An id that never existed is rejected the same way.
    r = client.put(f"/api/projects/{pid}/compute-profile", json={"profile": "gpu"})
    assert r.status_code == 422
