"""市场 catalog + a project's default execution environment."""

from tests.integration.conftest import post_project, session_auth_headers


def _project(client) -> str:
    return post_project(client, json={"name": "P"}, owner="alice").json()["data"]["id"]


def test_market_lists_ai_pools_and_sandbox_specs(client):
    data = client.get("/market/pools").json()["data"]
    assert {p["kind"] for p in data["ai"]} == {"ai"}
    assert {p["kind"] for p in data["compute"]} == {"compute"}
    ai_default = next(p for p in data["ai"] if p["default"])
    assert ai_default["available"] and ai_default["price"] and ai_default["description"]
    # The specs an agent's sandbox can have: the platform's, a VM of the
    # platform's, and people's own computers. Nothing that does not exist.
    assert {p["id"] for p in data["compute"]} == {"standard", "vm", "devices_of"}
    # Exactly one is what an agent nothing was chosen for gets.
    assert [p["id"] for p in data["compute"] if p["default"]] == ["standard"]
    assert all(p["price"] and p["description"] for p in data["compute"])
    # There is no room-level whole-machine choice to list any more.
    assert "visibility" not in data


def test_a_project_default_is_a_standard_sandbox_until_one_is_chosen(client):
    pid = _project(client)

    body = client.get(f"/projects/{pid}/compute-configs").json()["data"]
    assert body["default"] == {"kind": "sandbox", "spec": "standard"}

    # A default this project is not offered is refused, as is one that is not
    # an environment at all — no silent fallback.
    for default in (
        {"kind": "sandbox", "spec": "gpu"},
        {"kind": "sandbox", "spec": "devices_of:999999"},
        {"name": "Cloud", "profile": "cloud"},
    ):
        r = client.put(
            f"/projects/{pid}/compute-configs",
            json={"default": default},
            headers=session_auth_headers("alice"),
        )
        assert r.status_code in (400, 422), r.text
