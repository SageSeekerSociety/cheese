"""GET /sandbox/github-token — the GitHub credential a sandbox works with, and
the repo name that makes it usable.

A token on its own leaves the caller holding a credential with nothing to
point it at: `gh api repos/:owner/:repo/...` needs a name, and a topic
workspace is not a checkout of the connected repo, so there is nowhere else
to learn it from (the round trip that used to close this gap was
`GET /projects/{id}/github/connection`).

The permission line is the other half of that: an agent that has to discover
its own permissions by collecting 403s burns a turn doing it, and an agent
that believes a stale list asks a human to paste in what it could have read
itself. So the payload reports what this token really carries.
"""

import uuid

from app.core.sandbox_auth import mint_scoped_token
from app.domain.project.repositories import ProjectGitInstallationRepository


class _Installation:
    def __init__(self, repo: str) -> None:
        self.repo = repo
        self.installation_id = 4242


class _Minter:
    def __init__(self, permissions: dict[str, str]) -> None:
        self._permissions = permissions

    async def installation_token(self):
        return "ghs_installation", "2026-08-11T09:00:00Z"

    async def granted_permissions(self):
        return self._permissions


# What cheesex-app holds on SageSeekerSociety, plus the `issues: read` an admin
# may add: whatever the installation was granted is what the token carries.
_FULL = {
    "actions": "read",
    "checks": "read",
    "contents": "write",
    "issues": "read",
    "metadata": "read",
    "pull_requests": "write",
    "workflows": "write",
}


def _wire(
    monkeypatch,
    *,
    installation: _Installation | None,
    permissions: dict[str, str] | None = None,
) -> None:
    async def fake_get_by_project(_self, _project_id):
        return installation

    async def fake_tokens_for_project(_project_id, _session):
        return _Minter(permissions or _FULL) if installation is not None else None

    monkeypatch.setattr(
        ProjectGitInstallationRepository, "get_by_project", fake_get_by_project
    )
    monkeypatch.setattr(
        "app.api.routes.github_token.github_app_tokens_for_project",
        fake_tokens_for_project,
    )


def test_token_payload_names_the_repo_it_works_on(client, monkeypatch):
    project_id = str(uuid.uuid4())
    _wire(monkeypatch, installation=_Installation("acme/widgets"))

    r = client.get(
        "/sandbox/github-token",
        headers={"X-Cheese-Token": mint_scoped_token(project_id=project_id)},
    )

    assert r.status_code == 200
    data = r.json()["data"]
    assert data["token"] == "ghs_installation"
    assert data["repo"] == "acme/widgets"
    assert data["permissions"] == (
        "actions: read, checks: read, contents: write, issues: read, "
        "metadata: read, pull_requests: write, workflows: write"
    )


def test_the_payload_reports_the_permissions_this_token_really_has(client, monkeypatch):
    """Not a hardcoded list. An installation without `issues` must say so —
    telling an agent it can read issues when it can't just moves the failure
    to a 403 mid-turn, which is where the human-pasting habit came from."""
    project_id = str(uuid.uuid4())
    _wire(
        monkeypatch,
        installation=_Installation("acme/widgets"),
        permissions={"actions": "read", "checks": "read", "metadata": "read"},
    )

    r = client.get(
        "/sandbox/github-token",
        headers={"X-Cheese-Token": mint_scoped_token(project_id=project_id)},
    )

    assert (
        r.json()["data"]["permissions"] == "actions: read, checks: read, metadata: read"
    )


def test_the_payload_spells_out_the_level_not_just_the_name(client, monkeypatch):
    """This assertion used to be its opposite: the payload was required to
    start with "read-only: " and to contain no `write` anywhere.

    It was reversed deliberately (Zhifei, 2026-08-27): an agent is a full
    member of the room, and every stall this platform has had came from a
    credential that was too small, never from one that was too large
    (`docs/agent-principles.md` §2). Reporting the name without the level is
    the same failure in miniature — an agent that reads "contents" cannot tell
    whether it may push, so it either asks a human or finds out from a 403."""
    project_id = str(uuid.uuid4())
    _wire(monkeypatch, installation=_Installation("acme/widgets"))

    r = client.get(
        "/sandbox/github-token",
        headers={"X-Cheese-Token": mint_scoped_token(project_id=project_id)},
    )

    permissions = r.json()["data"]["permissions"]
    assert "contents: write" in permissions
    assert "pull_requests: write" in permissions
    # Pushing a branch that touches .github/workflows/* needs this one, and
    # GitHub's refusal names the permission rather than the file.
    assert "workflows: write" in permissions


def test_unconnected_project_gets_no_token(client, monkeypatch):
    project_id = str(uuid.uuid4())
    _wire(monkeypatch, installation=None)

    r = client.get(
        "/sandbox/github-token",
        headers={"X-Cheese-Token": mint_scoped_token(project_id=project_id)},
    )

    assert r.status_code >= 400


def test_unscoped_caller_is_rejected(client):
    r = client.get("/sandbox/github-token", headers={"X-Cheese-Token": "nonsense"})
    assert r.status_code == 401


def test_missing_binding_points_to_project_settings_without_requesting_pat(
    client, monkeypatch
):
    project_id = str(uuid.uuid4())
    _wire(monkeypatch, installation=None)
    response = client.get(
        "/sandbox/github-token",
        headers={"X-Cheese-Token": mint_scoped_token(project_id=project_id)},
    )
    assert response.status_code == 503
    message = response.json()["message"]
    assert f"/projects/{project_id}/settings" in message
    assert "connect their GitHub account" in message
    assert "Do not request a PAT" in message


def test_missing_app_config_is_distinct_from_missing_connection(client, monkeypatch):
    project_id = str(uuid.uuid4())
    _wire(monkeypatch, installation=_Installation("acme/widgets"))

    async def unavailable(*args):
        return None

    monkeypatch.setattr(
        "app.api.routes.github_token.github_app_tokens_for_project", unavailable
    )
    response = client.get(
        "/sandbox/github-token",
        headers={"X-Cheese-Token": mint_scoped_token(project_id=project_id)},
    )
    assert response.status_code == 503
    assert "repository is connected" in response.json()["message"]
