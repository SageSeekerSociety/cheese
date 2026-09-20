"""The git process serves repositories and nothing else.

Serving a repo is a different kind of work from answering the platform's API —
a pack for one project on dev was 169 MB and 3.7 seconds — so it runs in its
own process from the same image. What matters about that process is the two
halves of its boundary: it still refuses a caller who cannot prove a claim on
the project, and it does not carry the rest of the platform's surface.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.sandbox_auth import mint_scoped_token
from app.git_app import app


@pytest.fixture
def git_client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_it_still_refuses_a_caller_without_this_projects_token(git_client):
    project = uuid.uuid4()
    assert (
        git_client.get(f"/projects/{project}/git/info/refs?service=git-upload-pack")
    ).status_code == 401
    assert (
        git_client.get(
            f"/projects/{project}/git/info/refs?service=git-upload-pack",
            headers={"X-Cheese-Token": mint_scoped_token(project_id=str(uuid.uuid4()))},
        )
    ).status_code == 401, "another project's token is not a key to this one"


def test_the_platforms_other_routes_are_not_here(git_client):
    """A second door into the platform is what this process must not become."""
    for path in ("/topics", "/users/me", "/projects", "/connector/my/devices"):
        assert git_client.get(path).status_code == 404, path


def test_it_answers_its_own_health_check(git_client):
    assert git_client.get("/healthz").json() == {"status": "ok"}
