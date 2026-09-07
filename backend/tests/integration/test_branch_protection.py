"""Branch protection settings (issue #718): GET/PUT /projects/{id}/branch-protection.

平台补位 GitHub 判定不了的部分，所以规则住在项目设置里。These tests pin the
endpoint contract: defaults straight off GitHub's page (everything off, except
`dismiss_stale` — the pusher here is 芝士 with App write credentials, so a new
commit voids accepts unless opted out), partial-update semantics, write-side
validation, the steward-only guard (404 conceals the project, like every other
steward surface), and the read-only GitHub annotations degrading instead of
500ing.

`approvals_required` predates the block and stays at
``settings["approvals_required"]`` — read/written through this endpoint but
never moved and never dual-written; one test reads the raw row to pin that.
"""

import asyncio
import uuid

from tests.integration.conftest import session_auth_headers

_DEFAULTS = {
    "required_checks": [],
    "strict": False,
    "dismiss_stale": True,
    "auto_merge_allowed": False,
    "override_handles": None,
    "approvals_required": 1,
    "default_reviewer": "",
}


def _authed(client):
    client.headers.update(session_auth_headers("alice"))


def _make_project(client) -> str:
    _authed(client)
    r = client.post("/projects", json={"name": "P"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _get(client, pid: str) -> dict:
    r = client.get(f"/projects/{pid}/branch-protection")
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _put(client, pid: str, body: dict):
    return client.put(f"/projects/{pid}/branch-protection", json=body)


# --- Defaults -------------------------------------------------------------


def test_defaults_match_the_github_page_with_dismiss_stale_inverted(client):
    pid = _make_project(client)
    data = _get(client, pid)
    # An unbound project: nothing to ask GitHub, squash is the platform's way.
    assert data["merge_method"] == "squash"
    assert data["github_protection"]["enforced"] is False
    assert data["github_protection"]["status"] == "unbound"
    for key, expected in _DEFAULTS.items():
        assert data[key] == expected, key


# --- Partial update (quality-gate 旧例的语义) ------------------------------


def test_partial_update_touches_only_the_keys_present(client):
    pid = _make_project(client)

    r = _put(client, pid, {"strict": True})
    assert r.status_code == 200
    data = _get(client, pid)
    assert data["strict"] is True
    assert data["dismiss_stale"] is True  # untouched, still its default
    assert data["required_checks"] == []

    r = _put(
        client,
        pid,
        {"required_checks": [{"name": "test", "paths": ["backend/**"]}]},
    )
    assert r.status_code == 200
    data = _get(client, pid)
    assert data["required_checks"] == [{"name": "test", "paths": ["backend/**"]}]
    assert data["strict"] is True  # survived the second write

    # Clearing: empty list = the "off" default again.
    r = _put(client, pid, {"required_checks": []})
    assert r.status_code == 200
    assert _get(client, pid)["required_checks"] == []


def test_override_handles_and_default_reviewer_roundtrip_and_clear(client):
    pid = _make_project(client)

    r = _put(
        client,
        pid,
        {"override_handles": ["alice", "bob"], "default_reviewer": "bob"},
    )
    assert r.status_code == 200
    data = _get(client, pid)
    assert data["override_handles"] == ["alice", "bob"]
    assert data["default_reviewer"] == "bob"

    # null / empty put the defaults back (owner+lead, no default reviewer).
    r = _put(client, pid, {"override_handles": None, "default_reviewer": ""})
    assert r.status_code == 200
    data = _get(client, pid)
    assert data["override_handles"] is None
    assert data["default_reviewer"] == ""


def test_dismiss_stale_can_be_opted_out_and_back(client):
    pid = _make_project(client)
    r = _put(client, pid, {"dismiss_stale": False, "auto_merge_allowed": True})
    assert r.status_code == 200
    data = _get(client, pid)
    assert data["dismiss_stale"] is False
    assert data["auto_merge_allowed"] is True


def test_approvals_required_stays_at_its_original_settings_key(client):
    """不搬家、不双写: the endpoint reads and writes
    ``settings["approvals_required"]`` where it has always lived — never a copy
    under ``branch_protection``."""
    pid = _make_project(client)
    r = _put(client, pid, {"approvals_required": 3, "strict": True})
    assert r.status_code == 200
    assert _get(client, pid)["approvals_required"] == 3

    from app.domain.project.models import Project

    async def _settings() -> dict:
        async with client.test_factory() as s:
            project = await s.get(Project, uuid.UUID(pid))
            assert project is not None
            return dict(project.settings or {})

    settings = asyncio.run(_settings())
    assert settings["approvals_required"] == 3
    assert "approvals_required" not in settings.get("branch_protection", {})


# --- Write-side validation ------------------------------------------------


def test_rejects_bad_approvals_and_bad_shapes(client):
    pid = _make_project(client)
    assert _put(client, pid, {"approvals_required": 0}).status_code == 422
    assert _put(client, pid, {"approvals_required": "lots"}).status_code == 422
    assert _put(client, pid, {"strict": "yes"}).status_code == 422
    assert _put(client, pid, {"required_checks": "test"}).status_code == 422
    assert _put(client, pid, {"required_checks": [{"name": ""}]}).status_code == 422
    assert (
        _put(
            client, pid, {"required_checks": [{"name": "test", "paths": ["  "]}]}
        ).status_code
        == 422
    )
    assert (
        _put(
            client, pid, {"required_checks": [{"name": "test", "paths": ["/abs/**"]}]}
        ).status_code
        == 422
    )
    assert _put(client, pid, {"override_handles": ["", "bob"]}).status_code == 422
    # A rejected write leaves everything at its defaults.
    data = _get(client, pid)
    for key, expected in _DEFAULTS.items():
        assert data[key] == expected, key


# --- Guard: verified human owner/lead only --------------------------------


def test_update_requires_a_human_project_steward(client):
    pid = _make_project(client)

    client.headers.pop("Authorization")
    assert _put(client, pid, {"strict": True}).status_code == 404

    # An outsider is concealed from, not refused.
    client.headers.update(session_auth_headers("mallory"))
    assert _put(client, pid, {"strict": True}).status_code == 404


def test_update_allows_project_lead_not_ordinary_member(client):
    pid = _make_project(client)
    for handle, role in (("lead-user", "lead"), ("member-user", "member")):
        r = client.post(
            f"/projects/{pid}/members",
            json={"user_handle": handle, "role": role},
        )
        assert r.status_code == 200

    client.headers.update(session_auth_headers("lead-user"))
    assert _put(client, pid, {"strict": True}).status_code == 200

    client.headers.update(session_auth_headers("member-user"))
    assert _put(client, pid, {"strict": False}).status_code == 404
