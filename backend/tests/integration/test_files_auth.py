"""Who can read a project's files.

Same shape as the terminal routes: the handler checks that the project EXISTS,
which is not a check on the caller. Worth pinning explicitly because what these
routes return is the source itself, not a rendering of it.
"""

import uuid

import pytest


@pytest.mark.xfail(
    strict=True,
    reason=(
        "no caller check on these routes. The fix is a route dependency, but it "
        "must read the handle from the TOKEN CLAIMS the way _may_view_screen "
        "does — AuthUserInfo carries user_id only, and the session tokens in use "
        "are handle-only (user_id=None), so a user_id lookup 404s real callers."
    ),
)
def test_listing_a_projects_files_needs_a_credential(client):
    project = client.post(
        "/api/projects", json={"name": "Secret", "owner_handle": "alice"}
    ).json()["data"]

    resp = client.get(f"/api/projects/{project['id']}/files")

    assert resp.status_code in (401, 403, 404), (
        f"an unauthenticated caller got {resp.status_code}: {resp.text[:200]}"
    )


@pytest.mark.xfail(strict=True, reason="same hole as the listing route")
def test_reading_a_file_needs_a_credential(client):
    project = client.post(
        "/api/projects", json={"name": "Secret2", "owner_handle": "alice"}
    ).json()["data"]

    resp = client.get(
        f"/api/projects/{project['id']}/file", params={"path": "README.md"}
    )

    assert resp.status_code in (401, 403, 404), (
        f"an unauthenticated caller got {resp.status_code}: {resp.text[:200]}"
    )


@pytest.mark.xfail(
    strict=True,
    reason="the listing reads the jj workspace, which a push does not move",
)
def test_a_topic_worktree_listing_reflects_what_was_pushed(tmp_path, monkeypatch):
    """A machine that owns its tree pushes to the branch; the backend's jj
    workspace for that topic stays at the old commit. If the file list is read
    from that workspace, a remote machine's work is invisible in the file tree
    even though it landed."""
    from app.api.routes.git_http import _configure_for_push
    from app.domain.workspace import service as ws

    monkeypatch.setattr(ws.settings, "workspace_root", str(tmp_path / "ws"))
    project, topic = uuid.uuid4(), uuid.uuid4()
    repo = ws.ensure_repo(project)
    branch = ws.branch_for_topic(topic)
    ws._ensure_worktree(project, branch)
    _configure_for_push(repo)

    import subprocess

    work = tmp_path / "machine"
    for args in (
        ["clone", "-q", str(repo), str(work)],
        None,
    ):
        if args:
            subprocess.run(["git", *args], cwd=tmp_path, capture_output=True)
    for args in (
        ["config", "user.email", "c@z"],
        ["config", "user.name", "c"],
        ["checkout", "-q", "-B", branch, f"origin/{branch}"],
    ):
        subprocess.run(["git", *args], cwd=work, capture_output=True)
    (work / "made_on_the_machine.txt").write_text("hello\n")
    for args in (
        ["add", "-A"],
        ["commit", "-q", "-m", "w"],
        ["push", "origin", branch],
    ):
        subprocess.run(["git", *args], cwd=work, capture_output=True)

    listed = [
        f.get("path", f) if isinstance(f, dict) else f
        for f in ws.list_files(project, topic_id=topic)
    ]
    assert any("made_on_the_machine" in str(f) for f in listed), (
        f"pushed file missing from the topic listing: {listed}"
    )
