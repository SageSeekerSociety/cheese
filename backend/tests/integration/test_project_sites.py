"""Published sites contain accepted bytes and change only on explicit release."""

import asyncio
import shutil
import uuid
from pathlib import Path

import pytest

from app.core.config import settings
from app.domain.site.services import get_current_release, read_release_file
from app.domain.workspace import service as ws
from tests.integration.conftest import session_auth_headers
from tests.machine_work import machine_commits


@pytest.fixture(autouse=True)
def site_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "workspace"))
    monkeypatch.setattr(settings, "sites_domain", "sites.example.test")


def _project(client, *, owner="alice", team_id=None):
    body = {"name": "Published website", "owner_handle": owner}
    if team_id is not None:
        body["team_id"] = team_id
    response = client.post("/projects", json=body, headers=session_auth_headers(owner))
    assert response.status_code == 200, response.text
    return uuid.UUID(response.json()["data"]["id"])


def _accepted(pid, files):
    tid = uuid.uuid4()
    machine_commits(pid, tid, files)
    result = ws.merge_topic(
        pid, tid, message="Publish test website\n\nRequested-by: alice"
    )
    assert result["merged"] is True
    return ws.accepted_revision(pid)


def _get(client, pid, handle="alice"):
    return client.get(f"/projects/{pid}/site", headers=session_auth_headers(handle))


def _publish(client, pid, revision, *, directory="web", handle="alice"):
    return client.post(
        f"/projects/{pid}/site",
        json={"directory": directory, "expected_source_revision": revision},
        headers=session_auth_headers(handle),
    )


def _release(client, pid):
    async def read():
        async with client.test_factory() as session:
            return await get_current_release(session, pid)

    return asyncio.run(read())


def test_only_accepted_files_are_published_and_survive_machine_and_repo_removal(client):
    pid = _project(client)
    topic = uuid.uuid4()
    machine_commits(pid, topic, {"draft/index.html": "unaccepted"})
    assert _get(client, pid).json()["data"]["candidates"] == []
    accepted = {
        "web/index.html": (
            '<link rel="stylesheet" href="assets/a.css">'
            '<script type="module" src="assets/a.js"></script>'
        ),
        "web/assets/a.css": "body { color: blue }",
        "web/assets/a.js": "import('./lazy.js')",
        "web/assets/lazy.js": "export const answer = 42",
        "web/.env": "not a web asset",
    }
    revision = _accepted(pid, accepted)
    # A dirty main checkout must not leak into the immutable publication.
    ws.write_file(pid, "web/index.html", "dirty checkout")
    ws.write_file(pid, "web/untracked.txt", "untracked")
    state = _get(client, pid).json()["data"]
    assert state["source_revision"] == revision
    assert state["candidates"] == [{"directory": "web", "entry_file": "web/index.html"}]
    response = _publish(client, pid, revision)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["url"] == f"/sites/{pid}"
    release = _release(client, pid)
    assert release is not None
    shutil.rmtree(ws.ensure_repo(pid))
    for path, content in accepted.items():
        relative = path.removeprefix("web/")
        if relative != ".env":
            assert read_release_file(release, relative) == content.encode()
    assert read_release_file(release, ".env") is None
    assert read_release_file(release, "untracked.txt") is None
    assert read_release_file(release, "../index.html") is None


def test_new_acceptance_does_not_update_the_site_until_published(client):
    pid = _project(client)
    first_revision = _accepted(pid, {"web/index.html": "first"})
    assert _publish(client, pid, first_revision).status_code == 200
    first_release = _release(client, pid)
    next_revision = _accepted(
        pid, {"web/index.html": "second", "web/new.txt": "new asset"}
    )
    assert _get(client, pid).json()["data"]["site"]["source_revision"] == first_revision
    assert read_release_file(_release(client, pid), "") == b"first"
    assert _publish(client, pid, first_revision).status_code == 409
    assert _publish(client, pid, next_revision).status_code == 200
    assert read_release_file(_release(client, pid), "") == b"second"
    assert read_release_file(first_release, "") == b"first"


def test_failed_publication_preserves_previous_release(client, monkeypatch):
    pid = _project(client)
    first_revision = _accepted(pid, {"web/index.html": "first"})
    assert _publish(client, pid, first_revision).status_code == 200
    first = _release(client, pid)
    revision = _accepted(pid, {"web/index.html": "second"})
    original = Path.write_bytes

    def full_disk(path, data):
        if any(part.startswith(".staging-") for part in path.parts):
            raise OSError("disk full")
        return original(path, data)

    monkeypatch.setattr(Path, "write_bytes", full_disk)
    with pytest.raises(OSError, match="disk full"):
        _publish(client, pid, revision)
    current = _release(client, pid)
    assert current.id == first.id
    assert read_release_file(current, "") == b"first"
    assert not list(
        (Path(settings.workspace_root) / ".sites" / str(pid)).glob(".staging-*")
    )


def test_failed_commit_never_reports_publication_success(client, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    pid = _project(client)
    first_revision = _accepted(pid, {"web/index.html": "first"})
    assert _publish(client, pid, first_revision).status_code == 200
    first = _release(client, pid)
    revision = _accepted(pid, {"web/index.html": "second"})

    async def failed_commit(session):
        raise OSError("database commit failed")

    with monkeypatch.context() as failed_database:
        failed_database.setattr(AsyncSession, "commit", failed_commit)
        # Inspect the HTTP response a browser receives, including when teardown
        # raises after response headers have already been sent.
        failed_database.setattr(client._transport, "raise_server_exceptions", False)
        response = _publish(client, pid, revision)
    assert response.status_code == 500, response.text
    current = _release(client, pid)
    assert current.id == first.id
    assert read_release_file(current, "") == b"first"


def test_npm_and_source_entries_are_not_offered_as_working_sites(client):
    pid = _project(client)
    revision = _accepted(
        pid,
        {
            "web/index.html": '<script type="module" src="/src/main.tsx"></script>',
            "web/package.json": '{"scripts":{"build":"vite build"}}',
            "web/src/main.tsx": "export default <div />",
            "web/dist/index.html": '<script src="assets/app.js"></script>',
            "web/dist/assets/app.js": "document.body.textContent = 'built'",
        },
    )
    state = _get(client, pid).json()["data"]
    assert state["candidates"] == [
        {"directory": "web/dist", "entry_file": "web/dist/index.html"}
    ]
    assert _publish(client, pid, revision).status_code == 422
    assert _publish(client, pid, revision, directory="web/dist").status_code == 200


@pytest.mark.parametrize(
    "directory", ["../web", "/web", "web/../web", "web\\sub", ".git"]
)
def test_publication_cannot_escape_selected_project(client, directory):
    pid = _project(client)
    revision = _accepted(pid, {"web/index.html": "safe"})
    assert _publish(client, pid, revision, directory=directory).status_code == 422


def test_members_can_read_but_only_stewards_publish_even_when_legacy_authz_is_off(
    client, monkeypatch
):
    pid = _project(client)
    revision = _accepted(pid, {"web/index.html": "safe"})
    for handle, role in (("bob", "member"), ("carol", "lead")):
        response = client.post(
            f"/projects/{pid}/members",
            json={"user_handle": handle, "role": role},
            headers=session_auth_headers("alice"),
        )
        assert response.status_code == 200, response.text
    monkeypatch.setattr(settings, "authz_enforce_topic_access", False)
    assert _get(client, pid, "bob").status_code == 200
    assert _get(client, pid, "bob").json()["data"]["can_publish"] is False
    assert _publish(client, pid, revision, handle="bob").status_code == 403
    assert _get(client, pid, "mallory").status_code == 404
    assert _publish(client, pid, revision, handle="mallory").status_code == 404
    assert client.get(f"/projects/{pid}/site").status_code == 401
    assert _publish(client, pid, revision, handle="carol").status_code == 200


def test_unconfigured_host_never_offers_publication(client, monkeypatch):
    pid = _project(client)
    revision = _accepted(pid, {"web/index.html": "safe"})
    monkeypatch.setattr(settings, "sites_domain", "")
    state = _get(client, pid).json()["data"]
    assert state["can_publish"] is True
    assert state["unavailable_reason"]
    assert _publish(client, pid, revision).status_code == 422


def test_team_member_can_read_and_team_admin_can_publish(client):
    from tests.integration.test_team_member_enters_team_project import _team

    team_id = _team(client, owner="admin", members=("bob",))
    pid = _project(client, owner="alice", team_id=team_id)
    revision = _accepted(pid, {"web/index.html": "team site"})
    assert _get(client, pid, "bob").status_code == 200
    assert _publish(client, pid, revision, handle="bob").status_code == 403
    assert _get(client, pid, "admin").json()["data"]["can_publish"] is True
    assert _publish(client, pid, revision, handle="admin").status_code == 200
