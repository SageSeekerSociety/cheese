"""市场 catalog + per-project compute pool selection."""

from tests.integration.conftest import post_project


def _project(client) -> str:
    return post_project(client, json={"name": "P"}).json()["data"]["id"]


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
    """#282 §四: visibility is a catalog choice the platform SPEAKS, not a
    silent behaviour — a picker reads the warning straight from the catalog rather
    than the platform granting whole-machine access quietly.

    Both run; the isolated one is the default (#2320), and the whole machine is
    what a machine's owner gives a room."""
    data = client.get("/market/pools").json()["data"]
    vis = {v["id"]: v for v in data["visibility"]}
    assert {v["kind"] for v in data["visibility"]} == {"visibility"}

    assert vis["isolated"]["available"] is True
    assert vis["isolated"]["default"] is True
    assert vis["host"]["available"] is True
    assert vis["host"]["default"] is False

    # Exactly one default, and it runs.
    defaults = [v for v in data["visibility"] if v["default"]]
    assert len(defaults) == 1 and defaults[0]["available"] is True
    # The exact honest UI line #282 drafted, so the badge/tooltip copy is one source.
    assert "整台机器" in vis["host"]["description"]
    assert "其他房间" in vis["host"]["description"]


def test_project_default_names_a_machine_and_rejects_one_that_is_not_there(client):
    pid = _project(client)
    from app.core.config import settings
    from app.domain.agent.market import compute_default_name

    body = client.get(f"/projects/{pid}/compute-configs").json()["data"]
    # Nothing selected anywhere → the deployment's own fallback. With MicroCloud
    # unconfigured in this test that is the self-hosted pool; what matters is
    # that it names a machine this deployment has, never a retired one (#358).
    assert body["default"]["profile"] == compute_default_name(settings) == "device"

    # A default that is not a machine this deployment runs is rejected — no
    # silent fallback. The retired pool and an id that never existed alike.
    for profile in ("local-docker", "gpu"):
        r = client.put(
            f"/projects/{pid}/compute-configs",
            json={"default": {"name": profile, "profile": profile}},
        )
        assert r.status_code == 400, r.text
