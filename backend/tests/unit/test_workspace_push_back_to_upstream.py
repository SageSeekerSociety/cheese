"""采纳推回项目自己的远端时，落在**上游那条**分支上。

平台侧的基线恒为 `main`（`ensure_repo` 用 `git init -b main` 建仓，并立刻造一个
base commit），而上游的默认分支可以是 `master`——同步做的正是把 `upstream/master`
拉进本地的 `main`。两侧按同名推，一个默认分支叫 `master` 的 gitee / 校内 GitLab
项目会凭空多出一条谁也不看的 `main`，老师的 `master` 一个 commit 都收不到，而且
没有任何一句话告诉他：本来要消灭的那次沉默，换了个分支名活下来。

这里全是对磁盘上真 git 仓库的功能测试：一个 bare 仓库当老师的远端，断言的是那个
仓库里最后有哪些分支、各自指向哪个 commit。

同一个文件里还有写权限探测的那一条：它坐在卡片渲染的读路径上，所以断言的是「读
同一张卡两次，对方服务器只被连了一次」。
"""

import subprocess
import uuid
from pathlib import Path

import pytest

from app.domain.repository import service as ws


def _run(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True
    )
    assert result.returncode == 0, f"git {args}: {result.stderr or result.stdout}"
    return result.stdout


def _branches(bare: Path) -> list[str]:
    return sorted(
        line.strip()
        for line in _run(
            bare, "for-each-ref", "--format=%(refname:short)", "refs/heads"
        ).splitlines()
        if line.strip()
    )


@pytest.fixture
def workspace_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "ws"
    monkeypatch.setattr(ws.settings, "workspace_root", str(root))
    return root


def _teacher_repo(tmp_path: Path, default_branch: str) -> Path:
    """老师自己那个仓库：一个非 GitHub 的远端，默认分支叫它自己想叫的名字。"""
    bare = tmp_path / f"{default_branch}-upstream.git"
    subprocess.run(
        ["git", "init", "--bare", "-q", "-b", default_branch, str(bare)], check=True
    )
    seed = tmp_path / f"{default_branch}-seed"
    subprocess.run(["git", "clone", "-q", str(bare), str(seed)], check=True)
    (seed / "README.md").write_text("课程仓库\n", encoding="utf-8")
    _run(seed, "add", "-A")
    _run(
        seed, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "seed"
    )
    _run(seed, "push", "-q", "origin", f"HEAD:refs/heads/{default_branch}")
    return bare


def _project_synced_with(tmp_path: Path, bare: Path) -> uuid.UUID:
    """一个填了那个地址、点过同步的项目——老师看见历史进来了的那一刻。"""
    pid = uuid.uuid4()
    ws.set_upstream(pid, str(bare))
    assert ws.sync_upstream(pid)["synced"] is True
    return pid


def _accept_lands_a_commit(pid: uuid.UUID) -> str:
    """采纳合进平台仓库基线的那个 commit（squash 的结果，这里直接造一个）。"""
    repo = ws.ensure_repo(pid)
    (repo / "homework.py").write_text("print('done')\n", encoding="utf-8")
    _run(repo, "add", "-A")
    _run(
        repo,
        "-c",
        "user.email=t@t",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "-m",
        "feat: 学生的改动",
    )
    return _run(repo, "rev-parse", "HEAD").strip()


def _push_back(pid: uuid.UUID) -> str:
    """采纳那一步推回去时做的事（`_accept_external_remote`）。"""
    branch, _ = ws.base_branch_head(pid)
    ws.push_branch(pid, branch, None, remote_branch=ws.synced_upstream_branch(pid))
    return branch


def test_a_master_default_upstream_receives_the_commit_on_master(
    tmp_path, workspace_root
):
    """负向对照：上游的默认分支叫 `master`。

    推同名的那一版在这里就红了——老师的仓库会多出一条 `main`，而他的 `master`
    还停在 seed 上。
    """
    bare = _teacher_repo(tmp_path, "master")
    pid = _project_synced_with(tmp_path, bare)
    head = _accept_lands_a_commit(pid)

    assert _push_back(pid) == "main"

    assert _branches(bare) == ["master"], "老师的仓库里多出了一条没人看的分支"
    assert _run(bare, "rev-parse", "master").strip() == head


def test_a_main_default_upstream_receives_the_commit_on_main(tmp_path, workspace_root):
    """常态不变：上游也叫 `main` 时，推的还是 `main`。"""
    bare = _teacher_repo(tmp_path, "main")
    pid = _project_synced_with(tmp_path, bare)
    head = _accept_lands_a_commit(pid)

    _push_back(pid)

    assert _branches(bare) == ["main"]
    assert _run(bare, "rev-parse", "main").strip() == head


def test_an_upstream_nobody_synced_yet_takes_the_local_name(tmp_path, workspace_root):
    """还没同步过的空仓库上没有哪条分支可以对上，推本地这条名字。"""
    bare = tmp_path / "empty.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True)
    pid = uuid.uuid4()
    ws.set_upstream(pid, str(bare))
    head = _accept_lands_a_commit(pid)

    assert ws.synced_upstream_branch(pid) is None
    _push_back(pid)

    assert _branches(bare) == ["main"]
    assert _run(bare, "rev-parse", "main").strip() == head


# ---- 写权限探测：读一张卡不该对用户的服务器再发一次带鉴权的 push --------------


def test_reading_a_card_twice_asks_the_remote_once(
    tmp_path, workspace_root, monkeypatch
):
    """`can_push_upstream` 坐在 `describe()` 上，而 `describe()` 挂在十来个端点上。

    每次请求都真连一次，远端不可达时卡列表就由对方服务器决定响应时间（超时 15s，
    这段时间本次请求的 DB 会话一直开着），而推不动的项目每被打开一次就对老师那台
    GitLab 发一次失败鉴权——fail2ban 会因此锁账号或封 IP。
    """
    bare = _teacher_repo(tmp_path, "master")
    pid = uuid.uuid4()
    ws.set_upstream(pid, str(bare))

    # 「对对方服务器发了几次带鉴权的 push」＝有几条 git push 打在了那个远端上。
    reached: list[list[str]] = []
    real = ws._run_subprocess

    def _counted(argv, cwd, timeout, env=None):
        if "push" in argv and ws.UPSTREAM_REMOTE in argv:
            reached.append(list(argv))
        return real(argv, cwd, timeout, env)

    monkeypatch.setattr(ws, "_run_subprocess", _counted)

    assert ws.can_push_upstream(pid) is True
    assert ws.can_push_upstream(pid) is True
    assert ws.can_push_upstream(pid) is True

    assert len(reached) == 1, f"对方服务器被连了 {len(reached)} 次"


def test_a_new_upstream_address_is_asked_again(tmp_path, workspace_root):
    """换了地址就是换了一个问题，上一个地址的答案不算数。"""
    writable = _teacher_repo(tmp_path, "main")
    pid = uuid.uuid4()
    ws.set_upstream(pid, str(writable))
    assert ws.can_push_upstream(pid) is True

    ws.set_upstream(pid, str(tmp_path / "nowhere.git"))

    assert ws.can_push_upstream(pid) is False
