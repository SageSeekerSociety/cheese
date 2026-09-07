"""Upstream repo link + sync (spec §6.3 关联已有 repo): a project can bind an
existing git repo and pull its history in — the productized version of the
one-off dogfooding seed."""

import subprocess
from pathlib import Path

import pytest

from tests.conftest import stub_compute
from tests.machine_work import machine_commits


def _owner(client, handle: str = "alice") -> dict[str, str]:
    """The file routes return the source, so they need a caller with a claim on
    the project."""
    from tests.integration.test_connector_viewer import _login

    return {"Authorization": f"Bearer {_login(client, handle)}"}


def _project(client) -> str:
    return client.post("/projects", json={"name": "P", "owner_handle": "alice"}).json()[
        "data"
    ]["id"]


def _make_upstream(tmp_path: Path, name: str = "up") -> Path:
    """A real local git repo with one commit, to act as the upstream."""
    repo = tmp_path / name
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (repo / "hello.txt").write_text("hi from upstream\n")
    git("add", "-A")
    git("commit", "-q", "-m", "upstream: hello")
    return repo


def test_upstream_unset_by_default(client):
    pid = _project(client)
    r = client.get(f"/projects/{pid}/upstream")
    assert r.status_code == 200 and r.json()["data"]["url"] is None
    # Syncing without a link is a no-op with a clear reason, not an error.
    r = client.post(f"/projects/{pid}/upstream/sync")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["synced"] is False and "未关联" in d["reason"]


def test_link_sync_and_resync(client, tmp_path):
    up = _make_upstream(tmp_path)
    pid = _project(client)

    r = client.put(f"/projects/{pid}/upstream", json={"url": str(up)})
    assert r.status_code == 200 and r.json()["data"]["url"] == str(up)
    assert client.get(f"/projects/{pid}/upstream").json()["data"]["url"] == str(up)

    # First sync: unrelated histories merge in cleanly, upstream file appears.
    r = client.post(f"/projects/{pid}/upstream/sync")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["synced"] is True and d["commits"] >= 1
    r = client.get(
        f"/projects/{pid}/file",
        params={"path": "hello.txt"},
        headers=_owner(client),
    )
    assert r.status_code == 200
    assert "hi from upstream" in r.json()["data"]["content"]

    # Nothing new upstream → up to date, no merge commit spam.
    d = client.post(f"/projects/{pid}/upstream/sync").json()["data"]
    assert d["synced"] is True and d["commits"] == 0

    # New upstream commit → next sync picks it up.
    (up / "more.txt").write_text("again\n")
    subprocess.run(["git", "-C", str(up), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(up), "commit", "-q", "-m", "upstream: more"], check=True
    )
    d = client.post(f"/projects/{pid}/upstream/sync").json()["data"]
    assert d["synced"] is True and d["commits"] >= 1
    r = client.get(
        f"/projects/{pid}/file", params={"path": "more.txt"}, headers=_owner(client)
    )
    assert r.status_code == 200


def test_sync_conflict_aborts_and_names_files(client, tmp_path):
    import uuid as _uuid

    from app.domain.workspace import service as ws

    up = _make_upstream(tmp_path)
    pid = _project(client)
    # Give the project's main a commit whose hello.txt differs from upstream's —
    # the unrelated-histories merge then hits an add/add conflict.
    repo = ws.ensure_repo(_uuid.UUID(pid))
    (repo / "hello.txt").write_text("local version\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "local hello"], check=True
    )

    client.put(f"/projects/{pid}/upstream", json={"url": str(up)})
    d = client.post(f"/projects/{pid}/upstream/sync").json()["data"]
    assert d["synced"] is False
    assert "hello.txt" in d["reason"] and d["conflicts"] == ["hello.txt"]
    # Aborted cleanly: no half-merge left behind, local content intact.
    assert (repo / "hello.txt").read_text() == "local version\n"
    assert not (repo / ".git" / "MERGE_HEAD").exists()


def test_invalid_upstream_url_rejected(client):
    pid = _project(client)
    for bad in ["-x", "ext::sh -c id", "a b", "http://insecure", "file:///etc"]:
        r = client.put(f"/projects/{pid}/upstream", json={"url": bad})
        assert r.status_code == 422, bad


def test_unlink_upstream(client, tmp_path):
    up = _make_upstream(tmp_path)
    pid = _project(client)
    client.put(f"/projects/{pid}/upstream", json={"url": str(up)})
    r = client.put(f"/projects/{pid}/upstream", json={"url": ""})
    assert r.status_code == 200 and r.json()["data"]["url"] is None
    assert client.get(f"/projects/{pid}/upstream").json()["data"]["url"] is None


def test_accept_leaves_the_upstream_untouched(client, tmp_path):
    """Accepting merges into the platform's own repo and stops there (#718):
    the linked upstream receives no branch and no commit."""
    import uuid as _uuid

    up = _make_upstream(tmp_path)
    before = subprocess.run(
        ["git", "-C", str(up), "for-each-ref"], capture_output=True, text=True
    ).stdout

    pid = _project(client)
    client.put(f"/projects/{pid}/upstream", json={"url": str(up)})
    r = client.post(
        "/topics", json={"project_id": pid, "title": "T", "created_by": "u"}
    )
    tid = r.json()["data"]["id"]
    puid, tuid = _uuid.UUID(pid), _uuid.UUID(tid)
    machine_commits(puid, tuid, {"work.txt": "accepted work\n"})

    card = client.post(
        f"/topics/{tid}/accept-card",
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": "u",
            "routing_reason": "",
        },
    ).json()["data"]["id"]
    r = client.post(
        f"/accept-cards/{card}/accept",
        json={"decided_by": "u"},
        headers=_owner(client, "u"),
    )
    assert r.status_code == 200

    # The merge landed on the platform's base …
    from app.domain.workspace import service as ws

    assert ws.read_file(puid, "work.txt") == "accepted work\n"
    # … and the upstream is exactly as it was: no dogfood/<topic>, no new ref.
    after = subprocess.run(
        ["git", "-C", str(up), "for-each-ref"], capture_output=True, text=True
    ).stdout
    assert after == before


def test_accept_conflict_is_a_state_not_a_lie(client):
    """采纳冲突 (spec §6.3): a conflicting merge must NOT archive the topic —
    the card enters `conflict`, the workspace gets the materialized merge for
    芝士 to resolve, and a retry after resolution completes the accept."""
    import uuid as _uuid

    from app.domain.workspace import service as ws

    pid = _project(client)
    r = client.post(
        "/topics", json={"project_id": pid, "title": "T", "created_by": "u"}
    )
    tid = r.json()["data"]["id"]
    puid, tuid = _uuid.UUID(pid), _uuid.UUID(tid)

    # Branch edits f.txt one way…
    machine_commits(puid, tuid, {"f.txt": "branch version\n"})
    wt = ws.topic_worktree(puid, tuid)
    # …and base edits it the other way → guaranteed conflict.
    repo = ws.ensure_repo(puid)
    (repo / "f.txt").write_text("base version\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "base change"], check=True
    )

    card = client.post(
        f"/topics/{tid}/accept-card",
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": "u",
            "routing_reason": "",
        },
    ).json()["data"]["id"]
    r = client.post(
        f"/accept-cards/{card}/accept",
        json={"decided_by": "u"},
        headers=_owner(client, "u"),
    )
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["status"] == "conflict"
    assert "f.txt" in d["note"]
    # The topic is NOT archived — the work is not stranded silently.
    t = client.get(f"/topics/{tid}").json()["data"]
    assert t["status"] == "active"
    # The workspace holds the materialized conflict for 芝士.
    content = (wt / "f.txt").read_text()
    assert "<<<<<<<" in content or "base version" in content

    # 芝士 resolves it where it works — its own clone — and pushes the branch.
    machine_commits(puid, tuid, {"f.txt": "merged version\n"}, "解决采纳冲突")

    # Retry accept → clean merge, delivered (not archived), base has the resolution.
    r = client.post(
        f"/accept-cards/{card}/accept",
        json={"decided_by": "u"},
        headers=_owner(client, "u"),
    )
    assert r.status_code == 200 and r.json()["data"]["status"] == "accepted"
    t = client.get(f"/topics/{tid}").json()["data"]
    assert t["status"] == "active"
    assert t["accepted_at"] is not None
    assert ws.read_file(puid, "f.txt") == "merged version\n"


def test_upstream_conflict_materializes_and_accepting_completes_the_sync(
    client, tmp_path
):
    """同步上游冲突不是死路 (the gap this closes): 同步上游 aborting cleanly is
    correct for the shared repo, but on its own it leaves the project unable to
    ever pull — every later sync hits the same wall. The conflict must land in a
    topic's workspace with markers, and accepting that topic must finish the
    sync that aborted."""
    import uuid as _uuid

    from app.domain.workspace import service as ws

    up = _make_upstream(tmp_path)
    pid = _project(client)
    puid = _uuid.UUID(pid)

    # Local main and upstream both have hello.txt with different content → the
    # unrelated-histories merge is an add/add conflict.
    repo = ws.ensure_repo(puid)
    (repo / "hello.txt").write_text("local version\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "local hello"], check=True
    )
    client.put(f"/projects/{pid}/upstream", json={"url": str(up)})
    d = client.post(f"/projects/{pid}/upstream/sync").json()["data"]
    assert d["synced"] is False and d["conflicts"] == ["hello.txt"]

    # A topic to resolve it in, then materialize the conflict there.
    tid = client.post(
        "/topics", json={"project_id": pid, "title": "T", "created_by": "u"}
    ).json()["data"]["id"]
    tuid = _uuid.UUID(tid)
    files = ws.prepare_upstream_conflict_resolution(puid, tuid)
    assert files == ["hello.txt"]

    # Both sides are visible in the working copy — 芝士 can actually merge them
    # by hand rather than guessing which side to keep.
    body = (ws.topic_worktree(puid, tuid) / "hello.txt").read_text()
    assert "<<<<<<<" in body
    assert "local version" in body and "hi from upstream" in body

    # 芝士 resolves it where it works — its own clone — and pushes the branch.
    machine_commits(puid, tuid, {"hello.txt": "merged by hand\n"}, "解决同步上游冲突")

    card = client.post(
        f"/topics/{tid}/accept-card",
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": "u",
            "routing_reason": "",
        },
    ).json()["data"]["id"]
    r = client.post(
        f"/accept-cards/{card}/accept",
        json={"decided_by": "u"},
        headers=_owner(client, "u"),
    )
    assert r.status_code == 200 and r.json()["data"]["status"] == "accepted"

    # The resolution is on base…
    assert ws.read_file(puid, "hello.txt") == "merged by hand\n"
    # …and the sync is genuinely DONE: upstream is now an ancestor of base, so
    # the next sync has nothing left to bring over. This is the assertion that
    # proves the merge carried the upstream history, not just the file edit.
    d = client.post(f"/projects/{pid}/upstream/sync").json()["data"]
    assert d["synced"] is True and d["commits"] == 0


def test_sync_conflict_dispatches_cheese_at_the_materialized_merge(client, tmp_path):
    """The exit, wired to the button people actually press: a conflicting 同步上游
    by a logged-in caller creates a resolution task in their 1:1 room with 芝士
    and materializes the merge there. Without this the route reported a conflict
    and stopped, and the project could never pull again."""
    import uuid as _uuid

    from app.domain.workspace import service as ws

    up = _make_upstream(tmp_path)
    pid = _project(client)
    puid = _uuid.UUID(pid)
    repo = ws.ensure_repo(puid)
    (repo / "hello.txt").write_text("local version\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "local hello"], check=True
    )
    client.put(f"/projects/{pid}/upstream", json={"url": str(up)})

    d = client.post(
        f"/projects/{pid}/upstream/sync", headers=_owner(client, "alice")
    ).json()["data"]

    # The sync itself still tells the truth: aborted, and which files.
    assert d["synced"] is False and d["conflicts"] == ["hello.txt"]
    # …and now there is somewhere to go.
    assert d["dispatched"]["files"] == ["hello.txt"]
    tid = d["dispatched"]["topic_id"]

    # The work is real, carries the conflict in its workspace, and hangs in the
    # caller's 1:1 room rather than polluting the project's topic list.
    #
    # Read AS alice: it is a thread in a private room, and reading a thread is
    # authorized against the room it lives in — which is the point of a private
    # room. An anonymous read used to pass because the work was its own topic
    # and the private-ness stopped at the parent.
    t = client.get(f"/topics/{tid}", headers=_owner(client, "alice")).json()["data"]
    assert t["title"] == "解决同步上游冲突"
    body = (ws.topic_worktree(puid, _uuid.UUID(tid)) / "hello.txt").read_text()
    assert "<<<<<<<" in body
    assert "local version" in body and "hi from upstream" in body

    # The shared repo is untouched — dispatching must not half-merge either.
    assert (repo / "hello.txt").read_text() == "local version\n"
    assert not (repo / ".git" / "MERGE_HEAD").exists()

    # 芝士 is told to submit an accept card. Found the hard way in production
    # (2026-08-11): the first real dispatch resolved its conflict cleanly and
    # then stopped, because the prompt only described what acceptance WOULD do
    # and never asked for the card. With no card there is nothing to accept, so
    # a correct resolution sat in the workspace and the sync stayed stuck.
    #
    # Asserted on the prompt itself, NOT on the block it eventually becomes:
    # `runner.submit` is fire-and-forget, so reading the topic's blocks here is
    # a race — it passed locally and failed in CI on the very first run.
    from app.domain.workspace.upstream_conflict import _prompt

    prompt = _prompt(["hello.txt"], _uuid.UUID(tid))
    assert "验收卡" in prompt
    # 提示词是发给**房间**的：解冲突是一条活，而一条活是房间会话里的一个分身，
    # 它没有自己的会话可以叫醒。所以这段话得说清是哪条活、以及起完分身要 bind。
    assert tid in prompt, "不给 task id，房间没法把分身绑到这条活上"
    assert "cheese bind" in prompt


def test_second_sync_reuses_the_open_resolution_task(client, tmp_path):
    """Pressing 同步上游 again while a resolution is open must point back at it,
    not start a second one: re-materializing would overwrite whatever 芝士 has
    already resolved, and the room would fill with identical dead tasks."""
    import uuid as _uuid

    from app.domain.workspace import service as ws

    up = _make_upstream(tmp_path)
    pid = _project(client)
    repo = ws.ensure_repo(_uuid.UUID(pid))
    (repo / "hello.txt").write_text("local version\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "local hello"], check=True
    )
    client.put(f"/projects/{pid}/upstream", json={"url": str(up)})

    headers = _owner(client, "alice")
    first = client.post(f"/projects/{pid}/upstream/sync", headers=headers).json()[
        "data"
    ]["dispatched"]

    # 芝士 has started resolving — this content must survive a second press.
    wt = ws.topic_worktree(_uuid.UUID(pid), _uuid.UUID(first["topic_id"]))
    (wt / "hello.txt").write_text("half-resolved by 芝士\n")

    second = client.post(f"/projects/{pid}/upstream/sync", headers=headers).json()[
        "data"
    ]["dispatched"]
    assert second["topic_id"] == first["topic_id"] and second["reused"] is True
    assert (wt / "hello.txt").read_text() == "half-resolved by 芝士\n"


@pytest.mark.anyio
async def test_scheduler_syncs_linked_upstreams_with_nobody_pressing_the_button(
    client, tmp_path, monkeypatch
):
    """自动同步上游: the platform pulls upstream on its own interval.

    Falling behind is not a cosmetic problem — it is what makes accepting unable
    to push (a branch whose `.github/workflows/` differs from the default branch
    is rejected without the `workflows` permission), and 同步上游 has only ever
    been a button someone had to remember to press."""
    import uuid as _uuid

    from app.domain.agent.chat import ChatService
    from app.domain.scheduler.service import SchedulerService
    from app.domain.workspace import service as ws

    up = _make_upstream(tmp_path)
    pid = _project(client)
    client.put(f"/projects/{pid}/upstream", json={"url": str(up)})

    chat = ChatService(
        session_factory=client.test_factory,
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
        compute=stub_compute(),
    )
    seen_tokens: list[str | None] = []
    real_sync = ws.sync_upstream

    def spy(pid_, *, token=None):
        seen_tokens.append(token)
        return real_sync(pid_, token=token)

    monkeypatch.setattr(ws, "sync_upstream", spy)
    result = await SchedulerService(chat_service=chat).sync_upstreams()

    assert result["errors"] == []
    assert result["synced"] >= 1
    # Not just a status: the upstream's content is really on the project's base.
    assert ws.read_file(_uuid.UUID(pid), "hello.txt") == "hi from upstream\n"
    # No installation → no credential: an unbound project's fetch carries none.
    assert seen_tokens == [None]


class _AppReadTokens:
    """Stands in for the App's minter on a bound project."""

    async def installation_token(self) -> tuple[str, str]:
        return "ghs_read", "2099-01-01T00:00:00+00:00"


def _bind_to_app(monkeypatch) -> list[str | None]:
    """The project has an App installation; returns the tokens each
    sync_upstream call was handed, while the real sync still runs."""
    from app.domain.agent import github_app
    from app.domain.workspace import service as ws

    async def _tokens(_pid, _session):
        return _AppReadTokens()

    monkeypatch.setattr(github_app, "github_app_tokens_for_project", _tokens)
    seen: list[str | None] = []
    real_sync = ws.sync_upstream

    def spy(pid_, *, token=None):
        seen.append(token)
        return real_sync(pid_, token=token)

    monkeypatch.setattr(ws, "sync_upstream", spy)
    return seen


@pytest.mark.anyio
async def test_scheduler_fetches_a_bound_project_as_the_app(
    client, tmp_path, monkeypatch
):
    """A project the App is installed on fetches its upstream with the App's
    own token — the only credential the platform has for it (#718). The
    upstream here is a local repo, which proves the credential env does not get
    in the way of a fetch that needs none."""
    import uuid as _uuid

    from app.domain.agent.chat import ChatService
    from app.domain.scheduler.service import SchedulerService
    from app.domain.workspace import service as ws

    up = _make_upstream(tmp_path)
    pid = _project(client)
    client.put(f"/projects/{pid}/upstream", json={"url": str(up)})
    seen = _bind_to_app(monkeypatch)

    chat = ChatService(
        session_factory=client.test_factory,
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
        compute=stub_compute(),
    )
    result = await SchedulerService(chat_service=chat).sync_upstreams()

    assert result["errors"] == []
    assert seen == ["ghs_read"]
    assert ws.read_file(_uuid.UUID(pid), "hello.txt") == "hi from upstream\n"


def test_manual_sync_fetches_a_bound_project_as_the_app(client, tmp_path, monkeypatch):
    """The 同步上游 button takes the same credential path as the loop."""
    up = _make_upstream(tmp_path)
    pid = _project(client)
    client.put(f"/projects/{pid}/upstream", json={"url": str(up)})
    seen = _bind_to_app(monkeypatch)

    r = client.post(f"/projects/{pid}/upstream/sync")
    assert r.status_code == 200 and r.json()["data"]["synced"] is True
    assert seen == ["ghs_read"]


def _fetch_env(monkeypatch, tmp_path, *, token: str | None) -> dict:
    """The env the upstream fetch ran with. The sync is cut short right after
    the fetch — the credential is the whole question here."""
    import uuid as _uuid

    from app.core.errors import ValidationError
    from app.domain.workspace import service as ws

    fetches: list[dict] = []

    def fake_git(repo, *args, timeout=20, env=None):
        if args[0] == "fetch":
            fetches.append(dict(env or {}))
            return ""
        raise ValidationError("stop after the fetch")

    monkeypatch.setattr(ws, "ensure_repo", lambda pid: tmp_path)
    monkeypatch.setattr(
        ws, "get_upstream", lambda pid: "https://github.com/acme/widgets"
    )
    monkeypatch.setattr(ws, "_git", fake_git)
    ws.sync_upstream(_uuid.uuid4(), token=token)
    (env,) = fetches
    return env


def _credential_helpers(env: dict) -> list[str]:
    count = int(env.get("GIT_CONFIG_COUNT", "0"))
    return [
        env[f"GIT_CONFIG_VALUE_{i}"]
        for i in range(count)
        if env.get(f"GIT_CONFIG_KEY_{i}") == "credential.helper"
    ]


def test_bound_fetch_authenticates_through_the_env_and_nothing_on_disk(
    monkeypatch, tmp_path
):
    """The token reaches git through a credential helper wired in the env:
    never on argv, never a store file, and whatever helper the environment
    might already carry is reset so it cannot answer first."""
    env = _fetch_env(monkeypatch, tmp_path, token="ghs_read")
    helpers = _credential_helpers(env)
    assert helpers and helpers[0] == "", "the inherited helper list is reset"
    assert any(h for h in helpers), "an inline helper is configured"
    assert not any(h.startswith("store") for h in helpers)
    assert "ghs_read" in env.values()


def test_unbound_fetch_carries_no_credential(monkeypatch, tmp_path):
    env = _fetch_env(monkeypatch, tmp_path, token=None)
    assert _credential_helpers(env) == []
    assert "credential.helper" not in env.values()


@pytest.mark.anyio
async def test_scheduler_hands_a_conflicting_sync_to_cheese(client, tmp_path):
    """A conflict on the unattended path takes the same exit as the manual
    button: it becomes a task in the project owner's room with 芝士, rather than
    an error nobody sees. Without this the loop would just fail forever."""
    import uuid as _uuid

    from app.domain.agent.chat import ChatService
    from app.domain.scheduler.service import SchedulerService
    from app.domain.workspace import service as ws

    up = _make_upstream(tmp_path)
    pid = _project(client)
    puid = _uuid.UUID(pid)
    repo = ws.ensure_repo(puid)
    (repo / "hello.txt").write_text("local version\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "local hello"], check=True
    )
    client.put(f"/projects/{pid}/upstream", json={"url": str(up)})

    chat = ChatService(
        session_factory=client.test_factory,
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
        compute=stub_compute(),
    )
    result = await SchedulerService(chat_service=chat).sync_upstreams()

    assert result["dispatched"] == 1
    # The shared repo is untouched — an unattended sync must not half-merge.
    assert (repo / "hello.txt").read_text() == "local version\n"
    assert not (repo / ".git" / "MERGE_HEAD").exists()


# ---- 同步上游不再堆合并提交 (2026-08-16) -------------------------------------
#
# The sync was an unconditional `merge --no-ff`, so every tick minted a merge
# commit that existed only locally, nothing ever removed them, and every topic
# branch cut from the base carried the whole pile into its PR. Measured on
# 2026-08-16: 39 of PR #488's 40 commits were `同步上游 upstream/main → main`,
# and this repo's own base sat 41 such commits ahead of upstream with a
# byte-identical tree.


def _commit_upstream(up: Path, name: str, body: str) -> None:
    (up / name).write_text(body)
    subprocess.run(["git", "-C", str(up), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(up), "commit", "-q", "-m", f"upstream: {name}"], check=True
    )


def _base_log(repo: Path) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), "log", "--format=%s", "main"],
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.splitlines()


def test_repeated_syncs_add_no_commits_of_their_own(client, tmp_path):
    """The base branch is a MIRROR of the upstream's default branch. Ten syncs
    of ten upstream commits must leave ten commits — the upstream's own — and
    nothing the platform invented."""
    import uuid as _uuid

    from app.domain.workspace import service as ws

    up = _make_upstream(tmp_path)
    pid = _project(client)
    client.put(f"/projects/{pid}/upstream", json={"url": str(up)})
    client.post(f"/projects/{pid}/upstream/sync")

    for i in range(10):
        _commit_upstream(up, f"f{i}.txt", f"{i}\n")
        d = client.post(f"/projects/{pid}/upstream/sync").json()["data"]
        assert d["synced"] is True, d

    repo = ws.ensure_repo(_uuid.UUID(pid))
    subjects = _base_log(repo)
    assert not [s for s in subjects if "merge upstream" in s], subjects
    # Byte-identical to the upstream tip, and pointing AT it.
    assert (
        subprocess.run(
            ["git", "-C", str(repo), "diff", "--quiet", "main", "upstream/main"]
        ).returncode
        == 0
    )


def test_a_base_left_ahead_by_old_empty_merges_is_realigned(client, tmp_path):
    """The migration path, and why this is not just `merge --ff-only`: existing
    projects already carry the pile, and ff-only would refuse and mint one
    more. A base whose extra commits change nothing gets pointed at the
    upstream instead."""
    import uuid as _uuid

    from app.domain.workspace import service as ws

    up = _make_upstream(tmp_path)
    pid = _project(client)
    repo = ws.ensure_repo(_uuid.UUID(pid))
    client.put(f"/projects/{pid}/upstream", json={"url": str(up)})
    client.post(f"/projects/{pid}/upstream/sync")

    # Stand in for the old implementation's leftovers: commits on the base that
    # change not one byte.
    for i in range(3):
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", f"noop{i}"],
            check=True,
        )
    assert len(_base_log(repo)) > 1

    _commit_upstream(up, "next.txt", "next\n")
    d = client.post(f"/projects/{pid}/upstream/sync").json()["data"]

    assert d["synced"] is True
    assert not [s for s in _base_log(repo) if s.startswith("noop")]
    assert (
        subprocess.run(
            ["git", "-C", str(repo), "diff", "--quiet", "main", "upstream/main"]
        ).returncode
        == 0
    )


def test_local_content_the_upstream_lacks_is_merged_not_discarded(client, tmp_path):
    """The one case that still needs a real merge: a project seeded with work
    before it was bound. Fast-forwarding there would throw the work away."""
    import uuid as _uuid

    from app.domain.workspace import service as ws

    up = _make_upstream(tmp_path)
    pid = _project(client)
    repo = ws.ensure_repo(_uuid.UUID(pid))
    (repo / "mine.txt").write_text("local work\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "local: mine"], check=True
    )

    client.put(f"/projects/{pid}/upstream", json={"url": str(up)})
    d = client.post(f"/projects/{pid}/upstream/sync").json()["data"]

    assert d["synced"] is True, d
    assert (repo / "mine.txt").read_text() == "local work\n"
    assert (repo / "hello.txt").read_text() == "hi from upstream\n"
