"""A project's default execution environment."""

from tests.integration.conftest import post_project, session_auth_headers


def _project(client) -> str:
    return post_project(client, json={"name": "P"}, owner="alice").json()["data"]["id"]


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
