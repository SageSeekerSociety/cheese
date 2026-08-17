"""GET /sandbox/github-token — the read-only credential a sandbox uses to
read the repo it works on, and the repo name that makes it usable.

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

    async def readonly_token(self):
        return "ghs_readonly", "2026-08-11T09:00:00Z"

    async def sandbox_permissions(self):
        return self._permissions


# What the sandbox mint narrows to on an installation that grants `issues`.
_FULL = {
    "actions": "read",
    "checks": "read",
    "contents": "read",
    "issues": "read",
    "metadata": "read",
    "pull_requests": "read",
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
    assert data["token"] == "ghs_readonly"
    assert data["repo"] == "acme/widgets"
    assert data["permissions"] == (
        "read-only: actions, checks, contents, issues, metadata, pull_requests"
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

    assert r.json()["data"]["permissions"] == "read-only: actions, checks, metadata"


def test_the_sandbox_payload_never_advertises_write(client, monkeypatch):
    project_id = str(uuid.uuid4())
    _wire(monkeypatch, installation=_Installation("acme/widgets"))

    r = client.get(
        "/sandbox/github-token",
        headers={"X-Cheese-Token": mint_scoped_token(project_id=project_id)},
    )

    permissions = r.json()["data"]["permissions"]
    assert permissions.startswith("read-only: ")
    assert "write" not in permissions
    # The two grants the App holds that would let an agent rewrite the repo or
    # its CI. Neither is in the sandbox set, at any level.
    assert "workflows" not in permissions


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
