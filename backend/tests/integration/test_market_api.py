"""市场 catalog + per-project compute pool selection."""


def _project(client) -> str:
    return client.post("/projects", json={"name": "P"}).json()["data"]["id"]


def test_market_lists_ai_and_compute_pools(client):
    data = client.get("/market/pools").json()["data"]
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
    # Exactly one row is marked 默认, and it is the pool an unconfigured topic
    # actually runs on — the same function answers both.
    from app.core.config import settings
    from app.domain.agent.market import compute_default_name

    assert [p["id"] for p in data["compute"] if p["default"]] == [
        compute_default_name(settings)
    ]
    # Every listing still carries the browse-view fields.
    assert all(p["price"] and p["description"] for p in data["compute"])


def test_market_surfaces_the_whole_machine_visibility_choice_with_its_warning(client):
    """#282 §四 / #358: visibility is a catalog choice the platform SPEAKS, not a
    silent behaviour — a picker reads the warning straight from the catalog rather
    than the platform granting whole-machine access quietly.

    The default is whichever 档 can actually run. This test used to assert that
    `isolated` was the default AND undeployed, which is the contradiction #358's
    step-1 comment describes: the picker said a topic was boxed while
    `resolve_pinned_device` bound it to the whole machine. `host` is honestly the
    default until step 2 ships `isolated`'s transport, at which point both this
    catalogue and the resolver move together — they read one function."""
    data = client.get("/market/pools").json()["data"]
    vis = {v["id"]: v for v in data["visibility"]}
    assert {v["kind"] for v in data["visibility"]} == {"visibility"}

    assert vis["isolated"]["available"] is False  # transport is #358 step 2
    assert vis["isolated"]["default"] is False  # ...so it cannot be the default

    assert vis["host"]["available"] is True
    assert vis["host"]["default"] is True  # the only 档 with a transport today

    # The invariant that outlives today's answer: exactly one default, and it runs.
    defaults = [v for v in data["visibility"] if v["default"]]
    assert len(defaults) == 1 and defaults[0]["available"] is True
    # The exact honest UI line #282 drafted, so the badge/tooltip copy is one source.
    assert "整台机器" in vis["host"]["description"]
    assert "其他房间" in vis["host"]["description"]


def test_compute_profiles_default_and_reject_undeployed(client):
    pid = _project(client)
    from app.core.config import settings
    from app.domain.agent.market import compute_default_name

    body = client.get(f"/projects/{pid}/compute-profiles").json()["data"]
    # Nothing selected anywhere → the deployment's own fallback. With MicroCloud
    # unconfigured in this test that is the self-hosted pool; what matters is
    # that it names a machine this deployment has, never a retired one (#358).
    assert body["current"] == compute_default_name(settings) == "device"
    # With no device online and MicroCloud unconfigured in this test, nothing is
    # actually deployed, so there is nothing to offer.
    assert [p["id"] for p in body["profiles"]] == []

    # Selecting a pool that isn't deployed is rejected — no silent fallback. That
    # is the property this test exists for; the retired pool is a natural sample.
    r = client.put(f"/projects/{pid}/compute-profile", json={"profile": "local-docker"})
    assert r.status_code == 422

    # An id that never existed is rejected the same way.
    r = client.put(f"/projects/{pid}/compute-profile", json={"profile": "gpu"})
    assert r.status_code == 422
