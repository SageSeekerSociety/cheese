"""Anyone in a project may change how it works day to day, and everyone is told.

The project's main model and its new agents' environment are any member's to
change, not only its managers'. Each change leaves one line in the project's
综合, credited to whoever made it and saying what the setting was and is now;
that line is the record of the change. Someone outside the project changes
nothing, and what stays a manager's (the tier policy, which bounds what those
settings may cost) still refuses a member.
"""

import pytest

from app.core.config import settings
from tests.integration.conftest import (
    join_project_team,
    post_project,
    session_auth_headers,
)

VM = {"kind": "sandbox", "spec": "vm"}


@pytest.fixture
def project(client, monkeypatch):
    """alice's project, with bob on its team: a member who manages nothing.

    The deployment offers the platform's sandboxes, so a project has an
    environment to switch its new agents to."""
    monkeypatch.setattr(settings, "microcloud_base_url", "https://example.invalid")
    monkeypatch.setattr(settings, "microcloud_tenant_secret", "test-only")
    monkeypatch.setattr(settings, "microcloud_vm_offering_id", "vm-offering")
    data = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    join_project_team(client, data["id"], "bob")
    return data


def setting_lines(client, project) -> list[dict]:
    """The lines the project's 综合 shows about settings changing."""
    blocks = client.get(
        f"/topics/{project['root_topic_id']}/blocks",
        headers=session_auth_headers("alice"),
    ).json()["data"]["data"]
    return [
        b
        for b in blocks
        if (b.get("meta") or {}).get("event_type") == "setting_changed"
    ]


def models(client, pid) -> tuple[str, str]:
    """A model the team's plan allows, and one it does not."""
    choices = client.get(
        f"/projects/{pid}/default-model", headers=session_auth_headers("bob")
    ).json()["data"]["choices"]
    return (
        next(c["id"] for c in choices if c["allowed"]),
        next(c["id"] for c in choices if not c["allowed"]),
    )


def test_a_member_changes_the_main_model_and_the_new_agents_environment(
    client, project
):
    pid = project["id"]
    bob = session_auth_headers("bob")
    model, beyond_the_plan = models(client, pid)

    # The team's plan still bounds what a member may pick.
    refused = client.put(
        f"/projects/{pid}/default-model", json={"model": beyond_the_plan}, headers=bob
    )
    assert refused.status_code == 422, refused.text
    saved = client.put(
        f"/projects/{pid}/default-model", json={"model": model}, headers=bob
    )
    assert saved.status_code == 200, saved.text
    moved = client.put(
        f"/projects/{pid}/compute-configs", json={"default": VM}, headers=bob
    )
    assert moved.status_code == 200, moved.text

    alice = session_auth_headers("alice")
    assert (
        client.get(f"/projects/{pid}/default-model", headers=alice).json()["data"][
            "model"
        ]
        == model
    )
    assert (
        client.get(f"/projects/{pid}/compute-configs", headers=alice).json()["data"][
            "default"
        ]
        == VM
    )
    lines = setting_lines(client, project)
    assert [
        (
            line["author"],
            line["meta"]["setting"],
            line["meta"]["from"],
            line["meta"]["to"],
        )
        for line in lines
    ] == [
        ("bob", "default_model", None, model),
        ("bob", "execution_environment", None, VM),
    ]
    assert all(line["meta"]["by"] == "bob" for line in lines)
    assert all("<@bob>" in line["content"] for line in lines)
    # Nobody has to act on it: the line is there to be read, not delivered.
    assert all(line["meta"]["who"] == "platform" for line in lines)


def test_saving_a_setting_as_it_already_is_says_nothing(client, project):
    pid = project["id"]
    bob = session_auth_headers("bob")
    for _ in range(2):
        r = client.put(
            f"/projects/{pid}/task-naming", json={"mode": "manual"}, headers=bob
        )
        assert r.status_code == 200, r.text

    assert [line["meta"]["to"] for line in setting_lines(client, project)] == ["manual"]


def test_someone_outside_the_project_changes_nothing(client, project):
    pid = project["id"]
    eve = session_auth_headers("eve")

    for path, body in (
        ("default-model", {"model": models(client, pid)[0]}),
        ("compute-configs", {"default": VM}),
        ("task-naming", {"mode": "manual"}),
        ("forge-attribution", {"requester_coauthor": True}),
    ):
        refused = client.put(f"/projects/{pid}/{path}", json=body, headers=eve)
        assert refused.status_code == 403, (path, refused.text)

    assert setting_lines(client, project) == []


def test_what_bounds_the_cost_of_those_settings_stays_a_managers(client, project):
    pid = project["id"]
    policy = {"allowed_tiers": ["included"]}

    refused = client.put(
        f"/projects/{pid}/tier-policy", json=policy, headers=session_auth_headers("bob")
    )
    assert refused.status_code == 403, refused.text
    owner = client.put(
        f"/projects/{pid}/tier-policy",
        json=policy,
        headers=session_auth_headers("alice"),
    )
    assert owner.status_code == 200, owner.text
