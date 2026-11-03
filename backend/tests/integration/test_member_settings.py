"""Whoever may edit a project's settings changes how it works, and everyone is told.

The project's main model, its new agents' environment, how many turns run at
once, task naming and forge attribution are the project's settings: its owner
and admins change them by default, a plain member does not (#3830). Each change
leaves one line in the project's 综合, credited to whoever made it and saying
what the setting was and is now; that line is the record of the change. Someone
outside the project changes nothing.
"""

import pytest

from app.core.config import settings
from tests.integration.conftest import (
    approve_overseas,
    join_project_team,
    post_project,
    session_auth_headers,
)

VM = {"kind": "sandbox", "spec": "vm"}


@pytest.fixture
def project(client, monkeypatch):
    """alice's project, with bob on its team made its admin, and carol on its
    team as a plain member.

    The deployment offers the platform's sandboxes, so a project has an
    environment to switch its new agents to."""
    monkeypatch.setattr(settings, "microcloud_base_url", "https://example.invalid")
    monkeypatch.setattr(settings, "microcloud_tenant_secret", "test-only")
    monkeypatch.setattr(settings, "microcloud_vm_offering_id", "vm-offering")
    data = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    join_project_team(client, data["id"], "bob")
    # A second person moves the project into a shared team; ask that one, not
    # the personal team it was created in.
    team_id = join_project_team(client, data["id"], "carol")
    made = client.put(
        f"/projects/{data['id']}/members/bob/role",
        json={"role": "admin"},
        headers=session_auth_headers("alice"),
    )
    assert made.status_code == 200, made.text

    # Approved, so Claude is shown and its tier is what Free leaves out.
    async def approve() -> None:
        async with client.test_request_factory() as session:
            await approve_overseas(session, team_id)
            await session.commit()

    client.portal.call(approve)
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


def test_an_admin_changes_the_main_model_and_the_new_agents_environment(
    client, project
):
    pid = project["id"]
    bob = session_auth_headers("bob")
    model, beyond_the_plan = models(client, pid)

    # The team's plan still bounds what an admin may pick.
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


@pytest.mark.parametrize("who", ["eve", "carol"], ids=["outsider", "plain-member"])
def test_someone_without_the_permission_changes_nothing(client, project, who):
    pid = project["id"]
    headers = session_auth_headers(who)

    for path, body in (
        ("default-model", {"model": models(client, pid)[0]}),
        ("compute-configs", {"default": VM}),
        ("compute-configs", {"max_concurrent_turns": 40}),
        ("task-naming", {"mode": "manual"}),
        ("commit-credit", {"mode": "agent"}),
        ("tier-policy", {"allowed_tiers": ["included"]}),
    ):
        refused = client.put(f"/projects/{pid}/{path}", json=body, headers=headers)
        assert refused.status_code == 403, (path, refused.text)

    assert setting_lines(client, project) == []


def test_the_owner_sets_what_bounds_the_cost_of_those_settings(client, project):
    owner = client.put(
        f"/projects/{project['id']}/tier-policy",
        json={"allowed_tiers": ["included"]},
        headers=session_auth_headers("alice"),
    )
    assert owner.status_code == 200, owner.text


def compute_configs(client, pid, who="alice") -> dict:
    r = client.get(
        f"/projects/{pid}/compute-configs", headers=session_auth_headers(who)
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_how_many_of_a_projects_turns_may_run_at_once_is_an_admins_to_move(
    client, project
):
    """A project with sixteen task lines at once is what lifted the default:
    the number is on this project's own config, where anyone in it can read it
    and its admins say otherwise, and each move leaves one line in the 综合."""
    pid = project["id"]
    bob = session_auth_headers("bob")

    before = compute_configs(client, pid)
    assert before["max_concurrent_turns"] == settings.max_concurrent_turns
    assert before["max_concurrent_turns_from"] == "deployment"

    raised = client.put(
        f"/projects/{pid}/compute-configs",
        json={"max_concurrent_turns": 40},
        headers=bob,
    )
    assert raised.status_code == 200, raised.text
    assert raised.json()["data"]["max_concurrent_turns"] == 40
    # The other half of the config is untouched by a body that never mentions
    # it, and so is the environment.
    assert raised.json()["data"]["default"] == compute_configs(client, pid)["default"]

    own = compute_configs(client, pid)
    assert own["max_concurrent_turns"] == 40
    assert own["max_concurrent_turns_from"] == "project"
    assert [
        (line["meta"]["setting"], line["meta"]["from"], line["meta"]["to"])
        for line in setting_lines(client, project)
    ] == [("max_concurrent_turns", None, 40)]
    assert "40" in setting_lines(client, project)[0]["content"]

    # Null puts it back on the deployment's number, and that is a change too.
    cleared = client.put(
        f"/projects/{pid}/compute-configs",
        json={"max_concurrent_turns": None},
        headers=bob,
    )
    assert cleared.status_code == 200, cleared.text
    cleared_now = cleared.json()["data"]["max_concurrent_turns"]
    assert cleared_now == settings.max_concurrent_turns
    assert compute_configs(client, pid)["max_concurrent_turns_from"] == "deployment"
    assert [line["meta"]["to"] for line in setting_lines(client, project)] == [
        40,
        None,
    ]


def test_a_ceiling_outside_the_range_is_refused_with_the_range_in_the_refusal(
    client, project
):
    """A number nobody can act on is refused where it is read: 0 would admit no
    turn at all, and the top is what keeps a typo from asking one machine to
    absorb hundreds. The refusal names both ends, so a caller that guessed wrong
    learns the range from the answer."""
    pid = project["id"]
    bob = session_auth_headers("bob")
    kept = compute_configs(client, pid)

    for out_of_range in (0, 65, -1, 640):
        refused = client.put(
            f"/projects/{pid}/compute-configs",
            json={"max_concurrent_turns": out_of_range},
            headers=bob,
        )
        assert refused.status_code == 422, (out_of_range, refused.text)
        assert "1" in refused.json()["message"], out_of_range
        assert "64" in refused.json()["message"], out_of_range

    assert compute_configs(client, pid) == kept
    assert setting_lines(client, project) == []


def test_a_ceiling_that_is_not_a_number_is_refused_rather_than_coerced(client, project):
    """A JSON ``true`` is an ``int`` in Python, and a ``"40"`` is a digit string:
    either taken as a number would throttle the project to one turn at a time.
    The body is refused before the handler reads it, and the config does not
    move — the refusal is the framework's, not this route's, so it is a 400
    naming the field rather than the range.

    Every body here also carries a ``default`` the route accepts, so nothing but
    the wrong type can be what the refusal is about. With no strict field the
    ``True``, ``False`` and ``"40"`` bodies are read as integers instead of being
    refused as a wrong type, and this test is what catches that; the other three
    are refused as a wrong type either way."""
    pid = project["id"]
    bob = session_auth_headers("bob")
    kept = compute_configs(client, pid)

    for not_a_number in (True, False, "40", 1.5, [20], {"n": 20}):
        refused = client.put(
            f"/projects/{pid}/compute-configs",
            json={"default": VM, "max_concurrent_turns": not_a_number},
            headers=bob,
        )
        assert refused.status_code == 400, (not_a_number, refused.text)
        refused_at = refused.json()["error"]["data"]["details"]
        assert [error["loc"][-1] for error in refused_at] == ["max_concurrent_turns"], (
            not_a_number,
            refused.text,
        )

    assert compute_configs(client, pid) == kept
    assert setting_lines(client, project) == []


def test_a_body_that_names_only_the_environment_leaves_the_ceiling_alone(
    client, project
):
    """A body naming only ``default`` is the request this route took before the
    ceiling existed, so a project that has set its own ceiling is the case that
    matters: the setting must survive a body that never mentions it. Only
    ``null`` clears it."""
    pid = project["id"]
    bob = session_auth_headers("bob")

    raised = client.put(
        f"/projects/{pid}/compute-configs",
        json={"max_concurrent_turns": 40},
        headers=bob,
    )
    assert raised.status_code == 200, raised.text

    unchanged = client.put(
        f"/projects/{pid}/compute-configs",
        json={"default": VM},
        headers=bob,
    )
    assert unchanged.status_code == 200, unchanged.text
    kept = unchanged.json()["data"]
    assert kept["max_concurrent_turns"] == 40
    assert kept["max_concurrent_turns_from"] == "project"
    assert compute_configs(client, pid)["max_concurrent_turns"] == 40
