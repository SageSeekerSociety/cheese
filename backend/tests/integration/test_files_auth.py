"""Who can read a project's files.

Same shape as the terminal routes: the handler checks that the project EXISTS,
which is not a check on the caller. Worth pinning explicitly because what these
routes return is the source itself, not a rendering of it.
"""

import uuid


def test_listing_a_projects_files_needs_a_credential(client):
    project = client.post(
        "/api/projects", json={"name": "Secret", "owner_handle": "alice"}
    ).json()["data"]

    resp = client.get(f"/api/projects/{project['id']}/files")

    assert resp.status_code in (401, 403, 404), (
        f"an unauthenticated caller got {resp.status_code}: {resp.text[:200]}"
    )


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


def test_a_topic_worktree_listing_reflects_what_was_pushed(tmp_path, monkeypatch):
    """A machine that owns its tree pushes to the branch; the backend's jj
    workspace for that topic does not move on its own, so a read that goes
    through it showed nothing — the push looked like it had done nothing."""
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


def _push_a_file(repo, tmp_path, branch: str, name: str) -> None:
    """What a machine that owns its tree does."""
    import subprocess

    from app.api.routes.git_http import _configure_for_push

    _configure_for_push(repo)
    work = tmp_path / f"m-{name}"
    subprocess.run(["git", "clone", "-q", str(repo), str(work)], capture_output=True)
    for args in (
        ["config", "user.email", "c@z"],
        ["config", "user.name", "c"],
        ["checkout", "-q", "-B", branch, f"origin/{branch}"],
    ):
        subprocess.run(["git", *args], cwd=work, capture_output=True)
    (work / name).write_text("from the machine\n")
    for args in (
        ["add", "-A"],
        ["commit", "-q", "-m", name],
        ["push", "origin", branch],
    ):
        subprocess.run(["git", *args], cwd=work, capture_output=True)


def test_an_uncommitted_local_edit_is_never_swept_aside(tmp_path, monkeypatch):
    """Catching up must not cost a person the edit they are looking at.

    Someone editing in the workspace has changes that exist nowhere else. A
    fast-forward that discarded them would lose work to make a listing fresher,
    which is the wrong trade in every case.
    """
    from app.domain.workspace import service as ws

    monkeypatch.setattr(ws.settings, "workspace_root", str(tmp_path / "ws"))
    project, topic = uuid.uuid4(), uuid.uuid4()
    repo = ws.ensure_repo(project)
    branch = ws.branch_for_topic(topic)
    wt = ws._ensure_worktree(project, branch)

    (wt / "being_edited.txt").write_text("a human is typing here\n")
    _push_a_file(repo, tmp_path, branch, "from_machine.txt")

    listed = {f["path"] for f in ws.list_files(project, topic_id=topic)}

    assert "being_edited.txt" in listed, "the local edit was discarded"
    assert (wt / "being_edited.txt").read_text() == "a human is typing here\n"


def test_a_clean_workspace_picks_the_push_up(tmp_path, monkeypatch):
    from app.domain.workspace import service as ws

    monkeypatch.setattr(ws.settings, "workspace_root", str(tmp_path / "ws2"))
    project, topic = uuid.uuid4(), uuid.uuid4()
    repo = ws.ensure_repo(project)
    branch = ws.branch_for_topic(topic)
    ws._ensure_worktree(project, branch)

    _push_a_file(repo, tmp_path, branch, "landed.txt")

    listed = {f["path"] for f in ws.list_files(project, topic_id=topic)}
    assert "landed.txt" in listed
