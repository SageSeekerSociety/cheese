"""ExecutionProfile API: list selectable profiles + set a project's profile."""

from app.api.deps import get_profile_registry
from app.domain.agent.profiles import (
    TIER_DEFAULT,
    TIER_TESTING,
    AgentProfile,
    ProfileRegistry,
)


def _registry() -> ProfileRegistry:
    return ProfileRegistry(
        [
            AgentProfile("default", "Pool", TIER_DEFAULT, "glm-5.2", "https://gw", "k"),
            AgentProfile(
                "claude-opus", "Opus", TIER_TESTING, "claude-opus-4-8", None, "k2"
            ),
        ],
        "default",
        frozenset({"andyl"}),
    )


def _project(client, owner: str) -> str:
    return client.post("/projects", json={"name": "P", "owner_handle": owner}).json()[
        "data"
    ]["id"]


def test_non_dogfood_project_sees_only_default(client):
    client.app.dependency_overrides[get_profile_registry] = _registry
    try:
        pid = _project(client, "student-1")
        body = client.get(f"/projects/{pid}/execution-profiles").json()["data"]
        assert body["current"] == "default"
        assert [p["name"] for p in body["profiles"]] == ["default"]

        # Setting the testing profile is rejected for a non-dogfood owner.
        r = client.put(
            f"/projects/{pid}/execution-profile", json={"profile": "claude-opus"}
        )
        assert r.status_code == 422
    finally:
        client.app.dependency_overrides.pop(get_profile_registry, None)


def test_dogfood_owner_can_select_testing_profile(client):
    client.app.dependency_overrides[get_profile_registry] = _registry
    try:
        pid = _project(client, "andyl")
        names = [
            p["name"]
            for p in client.get(f"/projects/{pid}/execution-profiles").json()["data"][
                "profiles"
            ]
        ]
        assert names == ["default", "claude-opus"]

        r = client.put(
            f"/projects/{pid}/execution-profile", json={"profile": "claude-opus"}
        )
        assert r.status_code == 200
        assert r.json()["data"]["current"] == "claude-opus"
        # persisted
        cur = client.get(f"/projects/{pid}/execution-profiles").json()["data"][
            "current"
        ]
        assert cur == "claude-opus"
    finally:
        client.app.dependency_overrides.pop(get_profile_registry, None)
