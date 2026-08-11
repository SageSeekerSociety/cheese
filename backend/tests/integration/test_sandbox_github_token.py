"""GET /sandbox/github-token — the read-only credential a sandbox uses to
read CI, and the repo name that makes it usable.

A token on its own leaves the caller holding a credential with nothing to
point it at: `gh api repos/:owner/:repo/...` needs a name, and a topic
workspace is not a checkout of the connected repo, so there is nowhere else
to learn it from (the round trip that used to close this gap was
`GET /projects/{id}/github/connection`).
"""

import uuid

from app.core.sandbox_auth import mint_scoped_token
from app.domain.project.repositories import ProjectGitInstallationRepository


class _Installation:
    def __init__(self, repo: str) -> None:
        self.repo = repo
        self.installation_id = 4242


class _Minter:
    async def readonly_token(self):
        return "ghs_readonly", "2026-08-11T09:00:00Z"


def _wire(monkeypatch, *, installation: _Installation | None) -> None:
    async def fake_get_by_project(_self, _project_id):
        return installation

    async def fake_tokens_for_project(_project_id, _session):
        return _Minter() if installation is not None else None

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
    assert data["permissions"] == "read-only: actions, checks, metadata"


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
