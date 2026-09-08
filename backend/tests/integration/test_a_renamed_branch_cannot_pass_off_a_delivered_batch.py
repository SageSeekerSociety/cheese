"""一个分身改一下分支名，就能把已经交付的改动再交付一次吗？

「这一批交付过没有」如果建在**分支名**上，答案是能：`git branch -m` 不动任何一个
提交，却让平台答不出这条历史长在哪一批上，于是下一批的 PR 里重新出现上一批的文
件，报告还是 `status=ok`。名字是随手能改的，提交不是——所以这里问的是**提交**。

真实性说明：房间、批次、仓库、squash 合并、device 的 clone 和那条同步脚本全是真
的，平台的答案由**真实路由**给出（`_TheRealPlatform` 只是把它挂到一个 socket 上，
好让脚本里的 `curl` 够得着）。
"""

import json
import os
import subprocess
import threading
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.core.sandbox_auth import mint_scoped_token
from tests.integration.conftest import session_auth_headers


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=120
    )
    assert done.returncode == 0, f"git {' '.join(args)}: {done.stdout}{done.stderr}"
    return done.stdout


def _sync_body() -> str:
    from app.domain.agent.harness.claude_code.device_launch import build_launch_script

    return build_launch_script(sync_on_stop=True).split("'SYNC'")[1].split("SYNC")[0]


class _TheRealPlatform:
    """The room's branch endpoint, on a socket the device's `curl` can reach.

    Every answer is the route's own — the stand-in is the transport only, which
    is the point: what this file is about is what the PLATFORM says when a clone
    reports a branch name that was never a batch.
    """

    def __init__(self, client, url: str, token: str) -> None:
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler's own spelling
                query = self.path.partition("?")[2]
                answer = client.get(
                    f"{url}?{query}" if query else url,
                    headers={"X-Cheese-Token": token},
                )
                outer.asked.append(query)
                body = answer.content
                self.send_response(answer.status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        self.asked: list[str] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/branch"

    def stop(self) -> None:
        self._server.shutdown()


def _a_room_with_a_repo(client, tmp_path, monkeypatch) -> tuple[str, str, Path, str]:
    """一个项目、一个房间、一棵开着的树，外加它在磁盘上的仓库和那棵树的分支名。"""
    from app.core.config import settings
    from app.domain.workspace import service as ws

    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    client.headers.update(session_auth_headers("alice"))
    pid = client.post("/projects", json={"name": "改名不算数"}).json()["data"]["id"]
    room = client.post("/topics", json={"project_id": pid, "title": "房间"}).json()[
        "data"
    ]["id"]
    assert client.post(f"/topics/{room}/split", json={"title": "活"}).status_code == 200
    # Reaching the repo the way a device does is what configures it to take a
    # push (`_configure_for_push`), so this is not merely a convenience.
    answer = client.get(
        f"/projects/{pid}/git/branch/{room}",
        headers={"X-Cheese-Token": mint_scoped_token(project_id=pid)},
    )
    assert answer.status_code == 200, answer.text
    return pid, room, ws.ensure_repo(uuid.UUID(pid)), answer.json()["data"]["branch"]


def _device_clone(root: Path, remote: Path, branch: str) -> Path:
    work = root / "work"
    _git(root, "clone", "-q", str(remote), str(work))
    _git(work, "config", "user.email", "c@z")
    _git(work, "config", "user.name", "芝士")
    _git(work, "checkout", "-q", "-b", branch)
    return work


def _commit(work: Path, name: str, text: str) -> str:
    (work / name).write_text(text)
    _git(work, "add", "--", name)
    _git(work, "commit", "-qm", f"feat: {name}")
    return _git(work, "rev-parse", "HEAD").strip()


def _squash_into_main(repo: Path, root: Path, branch: str) -> str:
    """真的 squash 合并：main 拿到内容，而那条分支**不是** main 的祖先。"""
    staging = root / f"staging-{uuid.uuid4().hex[:8]}"
    _git(root, "clone", "-q", str(repo), str(staging))
    _git(staging, "config", "user.email", "p@z")
    _git(staging, "config", "user.name", "platform")
    _git(staging, "checkout", "-q", "main")
    _git(staging, "merge", "--squash", f"origin/{branch}")
    _git(staging, "commit", "-qm", "feat: batch one (#1)")
    _git(staging, "push", "-q", "origin", "main")
    return _git(staging, "rev-parse", "HEAD").strip()


def _deliver_and_open_the_next_batch(client, pid: str, room: str) -> None:
    """采纳落地之后房间的样子：上一批 merged，下一批是一棵新的开着的树。

    `delivered_head` 留空 —— 那些在平台开始记录交付点之前就合并掉的批次。
    """
    import asyncio

    from app.domain.room_task.models import TreeStatus, WorkTree
    from app.domain.room_task.services import WorkTreeService

    async def _do() -> None:
        async with client.test_factory() as session:
            from sqlalchemy import select

            tree = (
                await session.scalars(
                    select(WorkTree).where(
                        WorkTree.room_id == uuid.UUID(room),
                        WorkTree.status == TreeStatus.open,
                    )
                )
            ).one()
            tree.status = TreeStatus.merged
            tree.merged_at = datetime.now(UTC)
            tree.delivered_head = None
            await session.flush()
            await WorkTreeService(session).ensure_open(
                project_id=uuid.UUID(pid), room_id=uuid.UUID(room)
            )
            await session.commit()

    asyncio.run(_do())


def _turn(
    work: Path, sync: Path, url: str, remote: Path, log: Path, screen_branch: str
) -> str:
    """一轮结束时那个 Stop hook 报了什么。日志每轮先清空 —— 读累积日志再断言里面
    有 `"status":"ok"`，那个 ok 可能是上一轮的。"""
    log.write_text("")
    bindir = work.parent / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "cheese-hook").write_text(f'#!/bin/sh\ncat >> "{log}"\n')
    (bindir / "cheese-hook").chmod(0o755)
    done = subprocess.run(
        ["sh", str(sync)],
        env={
            **os.environ,
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "CHEESE_GIT_REMOTE": str(remote),
            # 屏幕启动那一刻冻住的值 —— 它说的还是上一批。
            "CHEESE_GIT_BRANCH": screen_branch,
            "CHEESE_BRANCH_URL": url,
            "CHEESE_WORK": str(work),
        },
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert done.returncode == 0, done.stderr
    return log.read_text()


def _branches(repo: Path) -> list[str]:
    return sorted(
        x.removeprefix("refs/heads/")
        for x in _git(repo, "for-each-ref", "--format=%(refname)").splitlines()
        if x.startswith("refs/heads/")
    )


def _pr_would_show(repo: Path, base: str, head: str) -> list[str]:
    """PR 打开时评审看到的东西 —— GitHub 的**三点** diff。两点看不出这个 bug：被
    重新交付的那一批内容和 main 上已有的一模一样。"""
    out = _git(repo, "diff", "--name-only", f"{base}...{head}")
    return sorted(x for x in out.splitlines() if x)


@pytest.mark.timeout(300)
def test_renaming_the_branch_does_not_make_a_delivered_batch_look_new(
    client, tmp_path, monkeypatch
):
    """andylizf 的复现，一步不差。

    这个 clone 上一批的分支已经真的 squash 进 main 了，而它是升级之前就在跑的
    机器 —— `branch` / `published` / `local-at` / `anchor` 四种记录一个都没有。然后
    **只改一个分支名**，一个提交都不动，再提交 `two.txt`。

    名字变了，历史一个字节没变：这条历史仍然长在已经进了 main 的那一批上。按名字
    判断的话平台答「这个名字从来不是本房间的批次」，于是普通推送、`status=ok`，新
    一批的 PR 里 `one.txt` 和 `two.txt` 一起出现 —— 上一批被再交付了一次。
    """
    pid, room, repo, first = _a_room_with_a_repo(client, tmp_path, monkeypatch)
    root = tmp_path / "machine"
    root.mkdir()
    work = _device_clone(root, repo, first)
    sync = root / "cheese-sync"
    sync.write_text(_sync_body())

    # 第一批：写、提交、按**升级之前**那个同步器的做法发布（什么记录都不留）。
    _commit(work, "one.txt", "batch one\n")
    _git(work, "push", "-q", "origin", f"HEAD:refs/heads/{first}")
    _squash_into_main(repo, root, first)
    _deliver_and_open_the_next_batch(client, pid, room)
    assert (
        _git(work, "for-each-ref", "--format=%(refname)").count("refs/cheese/") == 0
    ), "这个用例要的是一条本地记录都没有的 clone"
    assert not (work / ".git" / "cheese-sync" / "branch").exists()

    # 只改名字。一个提交都不动。
    _git(work, "branch", "-m", "dev/agent-work")
    _commit(work, "two.txt", "batch two\n")

    platform = _TheRealPlatform(
        client,
        f"/projects/{pid}/git/branch/{room}",
        mint_scoped_token(project_id=pid),
    )
    try:
        reported = _turn(work, sync, platform.url, repo, root / "hook.log", first)
    finally:
        platform.stop()

    next_batch = [b for b in _branches(repo) if b.startswith("topic/") and b != first]
    landed = {b: _pr_would_show(repo, "main", b) for b in next_batch}
    assert '"status":"ok"' not in reported, f"{reported}\nPR 会显示：{landed}"
    assert not any("one.txt" in files for files in landed.values()), (
        f"上一批的改动被当成新一批又交付了一次：{landed}"
    )
    # 内容一件没丢，而且报告说得出怎么取回来。
    detail = json.loads([x for x in reported.splitlines() if x.strip()][-1])["detail"]
    assert "git fetch origin" in detail, detail
    assert "refs/cheese/snapshots/" in detail, detail


@pytest.mark.timeout(300)
def test_a_dev_branch_that_was_never_a_batch_still_pushes_normally(
    client, tmp_path, monkeypatch
):
    """别误伤：分身自己起的 `dev/…`，提交从来没被记成任何一批的 tip —— 没有哪一批
    从它交付出去过，普通推送就是对的。

    房间**已经交付过一批**，所以这个 clone 的历史里躺着那次 squash 提交。按「祖先
    里有没有本房间的提交」去判就会把这里也挡下来，房间第一次同步就再也做不成。
    """
    pid, room, repo, first = _a_room_with_a_repo(client, tmp_path, monkeypatch)
    root = tmp_path / "machine"
    root.mkdir()
    seed = _device_clone(root, repo, first)
    _commit(seed, "one.txt", "batch one\n")
    _git(seed, "push", "-q", "origin", f"HEAD:refs/heads/{first}")
    _squash_into_main(repo, root, first)
    _deliver_and_open_the_next_batch(client, pid, room)

    # 一台全新的机器，从当前 main 起一条自己的分支。
    fresh = root / "fresh"
    _git(root, "clone", "-q", str(repo), str(fresh))
    _git(fresh, "config", "user.email", "c@z")
    _git(fresh, "config", "user.name", "芝士")
    _git(fresh, "checkout", "-q", "-b", "dev/my-own-branch")
    _commit(fresh, "mine.txt", "我自己起的分支上写的\n")
    sync = root / "cheese-sync"
    sync.write_text(_sync_body())

    platform = _TheRealPlatform(
        client,
        f"/projects/{pid}/git/branch/{room}",
        mint_scoped_token(project_id=pid),
    )
    try:
        reported = _turn(fresh, sync, platform.url, repo, root / "hook.log", first)
    finally:
        platform.stop()

    assert '"status":"ok"' in reported, reported
    opened = [b for b in _branches(repo) if b.startswith("topic/") and b != first]
    assert len(opened) == 1, opened
    assert _pr_would_show(repo, "main", opened[0]) == ["mine.txt"]


def _ask(client, pid: str, room: str, *, on: str, heads: list[str]) -> dict:
    """把 device 那一问原样问一遍：我在哪条分支上，我手里有哪些提交。"""
    answer = client.get(
        f"/projects/{pid}/git/branch/{room}",
        params={"on": on, "heads": ",".join(heads)},
        headers={"X-Cheese-Token": mint_scoped_token(project_id=pid)},
    )
    assert answer.status_code == 200, answer.text
    return answer.json()["data"]


@pytest.mark.timeout(300)
def test_the_batch_a_clone_is_told_about_is_the_last_one_delivered(
    client, tmp_path, monkeypatch
):
    """一个 clone 的历史里可以躺着**好几批**的交付点，答案只能是最后那一批。

    衔接是用 plumbing 做的，本地 HEAD 从不移动 —— 所以一台交付过两批的机器，它的
    祖先里第一批的 tip 一直都在。拿第一批当基线去接，第二批加的东西会被整批重新交
    付一次。
    """
    pid, room, repo, first = _a_room_with_a_repo(client, tmp_path, monkeypatch)
    root = tmp_path / "machine"
    root.mkdir()
    work = _device_clone(root, repo, first)

    tip_one = _commit(work, "one.txt", "batch one\n")
    _git(work, "push", "-q", "origin", f"HEAD:refs/heads/{first}")
    _squash_into_main(repo, root, first)
    _deliver_and_open_the_next_batch(client, pid, room)
    second = _ask(client, pid, room, on="", heads=[])["branch"]

    tip_two = _commit(work, "two.txt", "batch two\n")
    _git(work, "push", "-q", "origin", f"HEAD:refs/heads/{second}")
    _squash_into_main(repo, root, second)
    _deliver_and_open_the_next_batch(client, pid, room)

    said = _ask(client, pid, room, on=first, heads=[tip_two, tip_one])

    assert said["on_merged"] is True
    assert said["on_branch"] == second, (
        f"祖先里两批都在（{first} 和 {second}），答的却不是最后交付的那一批"
    )


@pytest.mark.timeout(300)
def test_a_batch_that_main_already_reaches_is_not_held_against_a_new_branch(
    client, tmp_path, monkeypatch
):
    """不是每次合并都是 squash：接上游历史那次是真合并（`merge_topic`），在 GitHub
    上按 merge commit 合的 PR 也是。那之后那一批的 tip 就是 main 的祖先，于是**每
    一个** clone 都带着它。

    把它也当成「这条历史长在一批已交付的活上」，房间里每一条新起的分支第一次同步
    都会被拒 —— 而它其实什么都没重复交付：main 已经用祖先关系包含了那些提交，三点
    diff 里一个字都不会再出现。
    """
    pid, room, repo, first = _a_room_with_a_repo(client, tmp_path, monkeypatch)
    root = tmp_path / "machine"
    root.mkdir()
    work = _device_clone(root, repo, first)
    tip_one = _commit(work, "one.txt", "batch one\n")
    _git(work, "push", "-q", "origin", f"HEAD:refs/heads/{first}")

    # 真合并，不是 squash：main 从此**包含**那一批的 tip。
    staging = root / "staging-merge"
    _git(root, "clone", "-q", str(repo), str(staging))
    _git(staging, "config", "user.email", "p@z")
    _git(staging, "config", "user.name", "platform")
    _git(staging, "checkout", "-q", "main")
    _git(staging, "merge", "--no-ff", "-q", "-m", "merge: batch one", f"origin/{first}")
    _git(staging, "push", "-q", "origin", "main")
    _deliver_and_open_the_next_batch(client, pid, room)
    assert (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", tip_one, "main"],
            cwd=repo,
            capture_output=True,
        ).returncode
        == 0
    ), "这个用例要的是一次**非** squash 的合并，否则它证明不了什么"

    said = _ask(client, pid, room, on="dev/my-own-branch", heads=[tip_one])

    assert said["on_merged"] is False, said
    assert said["on_branch"] == "", said
