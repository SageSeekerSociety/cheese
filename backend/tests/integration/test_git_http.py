"""Serving a project's repo over git's own protocol.

This is the direction that was missing: a machine cheese provisions owns its own
tree, so without it the agent's work never reached the topic branch — silently.
"""

import subprocess
import uuid
from pathlib import Path

from app.core.sandbox_auth import mint_scoped_token


def _project(client) -> str:
    return client.post("/projects", json={"name": "git 项目"}).json()["data"]["id"]


def test_a_project_token_advertises_that_projects_refs(client):
    pid = _project(client)
    token = mint_scoped_token(project_id=pid)
    r = client.get(
        f"/projects/{pid}/git/info/refs?service=git-upload-pack",
        headers={"X-Cheese-Token": token},
    )
    assert r.status_code == 200
    # git's advertisement always opens with the service pkt-line.
    assert b"# service=git-upload-pack" in r.content


def test_no_token_gets_nothing(client):
    pid = _project(client)
    r = client.get(f"/projects/{pid}/git/info/refs?service=git-upload-pack")
    assert r.status_code == 401, "this endpoint hands out a whole repository"


def test_another_projects_token_is_not_a_key_to_this_one(client):
    mine = _project(client)
    other = _project(client)
    r = client.get(
        f"/projects/{mine}/git/info/refs?service=git-upload-pack",
        headers={"X-Cheese-Token": mint_scoped_token(project_id=other)},
    )
    assert r.status_code == 401


def test_pushing_is_enabled_on_the_repo(client):
    """git-http-backend refuses receive-pack unless the repo opts in, so a push
    would 403 with everything else correct."""
    from app.domain.workspace import service as ws

    pid = _project(client)
    client.get(
        f"/projects/{pid}/git/info/refs?service=git-upload-pack",
        headers={"X-Cheese-Token": mint_scoped_token(project_id=pid)},
    )
    repo: Path = ws.ensure_repo(uuid.UUID(pid))
    value = subprocess.run(
        ["git", "config", "--get", "http.receivepack"],
        cwd=repo,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert value == "true"


def test_a_push_reaches_the_files_the_panel_reads(client):
    """A machine's push has to land in the topic's checkout, not just on the ref.

    The file panel, the diff and the sandbox all read files out of that
    checkout, so a ref that moved while the checkout stayed put shows the topic
    exactly as if the push had never happened — which is the failure this whole
    endpoint exists to prevent, and it cannot report itself (`cheese-sync` is a
    Stop hook; raising takes the turn down).
    """
    from app.domain.workspace import service as ws
    from tests.machine_work import machine_commits

    pid = _project(client)
    topic = uuid.uuid4()
    project = uuid.UUID(pid)
    worktree = ws.topic_worktree(project, topic)  # the topic is open
    # Reach the repo the way a machine does, so it is configured as one.
    client.get(
        f"/projects/{pid}/git/info/refs?service=git-upload-pack",
        headers={"X-Cheese-Token": mint_scoped_token(project_id=pid)},
    )

    machine_commits(project, topic, {"from_machine.txt": "what the machine wrote\n"})

    assert (worktree / "from_machine.txt").exists()
    assert "from_machine.txt" in [
        f["path"] for f in ws.list_files(project, topic_id=topic)
    ]


def test_a_push_still_lands_after_the_worktree_directory_is_deleted(client):
    """Disk cleanup must not quietly stop a machine from delivering.

    A topic's branch is checked out in a worktree, so git resolves a push
    against that directory. Delete it out of band — an operator reclaiming
    space, a wiped volume — and the branch is still registered to a directory
    that is not there: git fails trying to enter it and rejects every later
    push. The machine cannot report that (`cheese-sync` is a Stop hook), so the
    agent would go on working and delivering nothing at all.
    """
    import shutil

    from app.domain.workspace import service as ws
    from tests.machine_work import machine_commits

    pid = _project(client)
    project, topic = uuid.UUID(pid), uuid.uuid4()
    worktree = ws.topic_worktree(project, topic)
    shutil.rmtree(worktree)

    client.get(
        f"/projects/{pid}/git/info/refs?service=git-upload-pack",
        headers={"X-Cheese-Token": mint_scoped_token(project_id=pid)},
    )
    machine_commits(project, topic, {"delivered.txt": "the work\n"})

    listed = subprocess.run(
        [
            "git",
            "-C",
            str(ws.ensure_repo(project)),
            "ls-tree",
            "--name-only",
            ws.branch_for_tree(topic),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "delivered.txt" in listed


# --- 「这批活现在写哪条分支」---------------------------------------------


def _branch_url(project_id, topic_id) -> str:
    return f"/projects/{project_id}/git/branch/{topic_id}"


def test_a_devices_token_cannot_ask_about_another_projects_room(client):
    """凭据证明的是「我在这个项目里」，而地点 id 是全局解析的。

    少了这道一致性校验，拿 A 项目的 token 带上 B 项目某个房间的 id，就能问出
    B 在写哪条分支 —— 一个项目的凭据换来了另一个项目的信息。
    """
    from app.core.sandbox_auth import mint_scoped_token
    from tests.integration.conftest import session_auth_headers

    client.headers.update(session_auth_headers("alice"))
    mine = client.post("/projects", json={"name": "A"}).json()["data"]["id"]
    theirs = client.post("/projects", json={"name": "B"}).json()["data"]["id"]
    room = client.post(
        "/topics", json={"project_id": theirs, "title": "别人的房间"}
    ).json()["data"]["id"]
    assert client.post(f"/topics/{room}/split", json={"title": "活"}).status_code == 200

    r = client.get(
        _branch_url(mine, room),
        headers={"X-Cheese-Token": mint_scoped_token(project_id=mine)},
    )

    assert r.status_code == 404, r.text
    assert "topic/" not in r.text


def test_the_branch_is_only_told_to_a_token_for_that_project(client):
    from app.core.sandbox_auth import mint_scoped_token
    from tests.integration.conftest import session_auth_headers

    client.headers.update(session_auth_headers("alice"))
    pid = client.post("/projects", json={"name": "A"}).json()["data"]["id"]
    room = client.post("/topics", json={"project_id": pid, "title": "房间"}).json()[
        "data"
    ]["id"]
    assert client.post(f"/topics/{room}/split", json={"title": "活"}).status_code == 200

    without = client.get(_branch_url(pid, room))
    assert without.status_code == 401, without.text

    with_token = client.get(
        _branch_url(pid, room),
        headers={"X-Cheese-Token": mint_scoped_token(project_id=pid)},
    )
    assert with_token.status_code == 200, with_token.text
    assert with_token.json()["data"]["branch"].startswith("topic/")


def test_a_refused_cross_project_ask_does_not_touch_that_projects_marker(client):
    """拒绝之前不许先写。

    解析一个地点会**顺手修**它磁盘上那条「这个房间写哪棵树」的记号，这是个写副
    作用。校验放在解析之后，就等于在拒绝一个外项目请求之前，已经先改了那个外项目
    的东西 —— 一个越权的读被拒了，越权的写却已经发生了。
    """
    import uuid as _uuid

    from app.core.sandbox_auth import mint_scoped_token
    from app.domain.workspace import service as ws
    from tests.integration.conftest import session_auth_headers

    client.headers.update(session_auth_headers("alice"))
    mine = client.post("/projects", json={"name": "A"}).json()["data"]["id"]
    theirs = client.post("/projects", json={"name": "B"}).json()["data"]["id"]
    room = client.post(
        "/topics", json={"project_id": theirs, "title": "别人的房间"}
    ).json()["data"]["id"]
    assert client.post(f"/topics/{room}/split", json={"title": "活"}).status_code == 200
    # 把那个项目的记号按到一个别的值上，然后看它有没有被动过。
    skew = _uuid.uuid4()
    ws.bind_tree(_uuid.UUID(room), skew)

    r = client.get(
        _branch_url(mine, room),
        headers={"X-Cheese-Token": mint_scoped_token(project_id=mine)},
    )

    assert r.status_code == 404, r.text
    assert ws.tree_for_place(_uuid.UUID(room)) == skew, (
        "拒绝这次跨项目请求之前，已经改了那个项目的记号"
    )


def test_a_branch_this_room_never_delivered_from_is_not_a_merged_batch(client):
    """`on_merged` 说的是「`?on=` 那个名字是这个房间**合并过**的一批」，不是「那个
    名字和现在这批不一样」。

    分身自己起的 `dev/…`、clone 落在的基线分支，名字都对不上当前批次，而从它们身上
    没有哪一批交付出去过 —— device 照常推就是对的。把这两种情况混成一个答案，房间
    第一次同步就会被当成「衔接失败」挡住。
    """
    from app.core.sandbox_auth import mint_scoped_token
    from tests.integration.conftest import session_auth_headers

    client.headers.update(session_auth_headers("alice"))
    pid = client.post("/projects", json={"name": "A"}).json()["data"]["id"]
    room = client.post("/topics", json={"project_id": pid, "title": "房间"}).json()[
        "data"
    ]["id"]
    assert client.post(f"/topics/{room}/split", json={"title": "活"}).status_code == 200

    answer = client.get(
        f"{_branch_url(pid, room)}?on=dev/my-own-branch",
        headers={"X-Cheese-Token": mint_scoped_token(project_id=pid)},
    )

    assert answer.status_code == 200, answer.text
    said = answer.json()["data"]
    assert said["on_merged"] is False
    assert said["on_head"] == ""
    assert said["branch"].startswith("topic/")
