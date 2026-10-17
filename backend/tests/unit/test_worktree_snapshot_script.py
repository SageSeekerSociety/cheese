"""随 cheese 技能下发的 `worktree-snapshot.sh`：它说「存好了」时，远端上真有。

这个脚本守的是一条已经发生过的事故：跑会话的机器被无预警回收，`$HOME` 一起清
掉，任务分支没推到远端，这一轮的活就没了。它自己不做快照——快照和恢复是平台
的事（`cheese sync` / `cheese worktree`）——它做的是跑那条路，然后当场核对。

所以这里只从命令行调它，用一个假的 `cheese` 按平台的契约演一遍
（`backend/sandbox/cheese` 的 `_apply_snapshot` 与 `_task_worktree`），断言它自
己的行为：

  * 远端没有那个提交、或者干脆连不上时，不许说「核对通过」；
  * 不在任务分支上、分离头指针时拒判，并把该跑什么说清楚；
  * 交给人的恢复命令必须是完整任务 id——`cheese worktree` 只认 UUID；
  * 接回来时本地合法地可能比远端多几个没推上去的提交，这不是失败；落后或分叉
    才是，而且要在动更多东西之前停下；
  * 未提交的改动不许被它推进任务分支——那是 PR 的内容，不是备份的内容；
  * 换机模拟：整个工作目录删掉，从远端接回来，未提交的东西也在。

假 `cheese` 只实现两个子命令的对外行为，不读脚本的实现。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.domain.skill_library import _NATIVE_SKILL_SRC

SCRIPT = _NATIVE_SKILL_SRC / "cheese" / "scripts" / "worktree-snapshot.sh"

TASK = "61525614-6c61-4c15-8b37-f4d3efc94f3c"
SHORT = TASK.split("-")[0]
BRANCH = f"task/{SHORT}"

GIT_ENV = {
    "GIT_AUTHOR_NAME": "snapshot test",
    "GIT_AUTHOR_EMAIL": "snapshot@test.invalid",
    "GIT_COMMITTER_NAME": "snapshot test",
    "GIT_COMMITTER_EMAIL": "snapshot@test.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_TERMINAL_PROMPT": "0",
}

# 按平台自己的备份/恢复契约演一遍，只到脚本用得到的那几步：
#   sync     -> backend/sandbox/cheese 的 _backup_task_snapshot
#               （临时索引 + commit-tree）
#   worktree -> 同文件的 _task_worktree 与 _apply_snapshot
FAKE_CHEESE = r"""#!/usr/bin/env bash
set -euo pipefail

snapshot_ref() {
  printf 'refs/cheese/snapshots/%s' "${CHEESE_TOPIC:?}"
}

case ${1:-} in
  sync)
    [ "${FAKE_SYNC_NOOP:-}" = 1 ] && exit 0
    root=$(git rev-parse --show-toplevel)
    cd "$root"
    branch=$(git symbolic-ref --quiet --short HEAD)
    head=$(git rev-parse HEAD)
    idx="$root/.git/fake-snapshot-index"
    rm -f "$idx"
    GIT_INDEX_FILE="$idx" git read-tree HEAD
    GIT_INDEX_FILE="$idx" git add -A
    tree=$(GIT_INDEX_FILE="$idx" git write-tree)
    rm -f "$idx"
    snap=$(git commit-tree "$tree" -p "$head" -m "cheese snapshot")
    git push -q "$FAKE_REMOTE" "$head:refs/heads/$branch"
    # 平台每备份一次就做一个新的快照提交，所以这个 ref 是往前顶的（真实现是本
    # 地用 update-ref + 传 bundle，这里用 --force 推同一个 ref 代替）。
    git push -q --force "$FAKE_REMOTE" "$snap:$(snapshot_ref)"
    ;;
  worktree)
    id=${2:?}
    # 真 CLI 第一件事就是 uuid.UUID(task_id)，短 id 在这里当场失败。
    uuid='[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
    printf '%s' "$id" | grep -Eqx "$uuid" || {
      echo "fake cheese: badly formed UUID string: $id" >&2
      exit 1
    }
    short=${id%%-*}
    dir=${FAKE_WORKTREE_DIR:?}
    [ -d "$dir/.git" ] || git clone -q "$FAKE_REMOTE" "$dir"
    cd "$dir"
    git checkout -q -B "task/$short" "origin/task/$short"
    if [ "${FAKE_WORKTREE_CHATTER:-}" = 1 ]; then
      echo "[cheese] 新环境，正在接回任务 $id 的备份" >&2
    fi
    if [ "${FAKE_WORKTREE_STALE:-}" = 1 ]; then
      # 接回来的状态落后于远端：脚本必须停下来，不能再改这个目录。
      git reset -q --hard HEAD~1
    else
      snap=$(git ls-remote origin "$(snapshot_ref)" | cut -f1)
      if [ -n "$snap" ]; then
        git fetch -q origin "+$(snapshot_ref)"
        saved=$(git rev-parse FETCH_HEAD)
        head=$(git rev-parse FETCH_HEAD^)
        here=$(git rev-parse HEAD)
        if [ "$head" != "$here" ] && git merge-base --is-ancestor "$here" "$head"; then
          git merge -q --ff-only "$head" || true
        fi
        git read-tree -m -u "$head" "$saved" 2>/dev/null || true
        git reset -q
      fi
    fi
    pwd
    ;;
  *)
    echo "fake cheese: 不认识 $1" >&2
    exit 1
    ;;
esac
"""


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env={**os.environ, **GIT_ENV},
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _env(**extra: str) -> dict[str, str]:
    return {**os.environ, **GIT_ENV, **extra}


def ran(result: subprocess.CompletedProcess[str]) -> str:
    assert result.returncode == 0, result.stderr + result.stdout
    return result.stdout


def refused(result: subprocess.CompletedProcess[str], code: int, *said: str) -> None:
    assert result.returncode == code, result.stderr + result.stdout
    for text in said:
        assert text in result.stderr, result.stderr


class Machine:
    """一台会过期的机器：一个临时远端、一个任务目录、一个假 cheese。"""

    def __init__(self, root: Path) -> None:
        self.tmp = root
        self.home = root / "home"
        (self.home / "room").mkdir(parents=True)
        self.remote = root / "origin.git"
        # 平台把任务目录建在 ~/.cheese/tasks/<完整 id>，脚本从目录名取 id。
        self.worktree = self.home / ".cheese" / "tasks" / TASK
        self.bin = root / "bin"
        self.bin.mkdir()
        self.fake_cheese = self.bin / "cheese"
        self.fake_cheese.write_text(FAKE_CHEESE)
        self.fake_cheese.chmod(0o755)

        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True)
        subprocess.run(
            ["git", "init", "-q", "-b", "main", str(self.worktree)], check=True
        )
        (self.worktree / "README.md").write_text("base\n")
        git("add", "README.md", cwd=self.worktree)
        git("commit", "-q", "-m", "base", cwd=self.worktree)
        git("remote", "add", "origin", str(self.remote), cwd=self.worktree)
        git("push", "-q", "-u", "origin", "main", cwd=self.worktree)
        git("switch", "-q", "-c", BRANCH, cwd=self.worktree)

    def run(
        self, *args: str, cwd: Path | None = None, **extra_env: str
    ) -> subprocess.CompletedProcess[str]:
        where = cwd or (self.worktree if self.worktree.is_dir() else self.tmp)
        env = {
            **os.environ,
            **GIT_ENV,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "HOME": str(self.home),
            "CHEESE_TOPIC": TASK,
            "FAKE_REMOTE": str(self.remote),
            "FAKE_WORKTREE_DIR": str(self.worktree),
            **extra_env,
        }
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            cwd=where,
            env=env,
            capture_output=True,
            text=True,
        )

    def wipe(self) -> None:
        """工作机被回收：整个任务目录没了。"""
        shutil.rmtree(self.worktree)

    def remote_tip(self, ref: str) -> str:
        out = subprocess.run(
            ["git", "ls-remote", str(self.remote), ref],
            env=_env(),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return out.split("\t")[0] if out else ""

    def commit(self, name: str, body: str) -> None:
        (self.worktree / name).write_text(body)
        git("add", name, cwd=self.worktree)
        git("commit", "-q", "-m", f"add {name}", cwd=self.worktree)

    def head(self) -> str:
        return git("rev-parse", "HEAD", cwd=self.worktree)

    def snapshot_without_pushing_the_branch(self) -> None:
        """远端有一条快照，而它的分支提交没推上去——换机后接回来的正是这种。

        平台快照存的是当时的 HEAD，分支推没推上去是另一回事，所以本地接回来
        以后合法地会比远端多几个提交。
        """
        head = self.head()
        idx = self.worktree / ".git" / "test-snapshot-index"
        env = _env(GIT_INDEX_FILE=str(idx))
        for args in (["read-tree", "HEAD"], ["add", "-A"]):
            subprocess.run(
                ["git", *args],
                cwd=self.worktree,
                env=env,
                check=True,
                capture_output=True,
            )
        tree = subprocess.run(
            ["git", "write-tree"],
            cwd=self.worktree,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        idx.unlink(missing_ok=True)
        snap = subprocess.run(
            ["git", "commit-tree", tree, "-p", head, "-m", "cheese snapshot"],
            cwd=self.worktree,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        git(
            "push",
            "-q",
            "--force",
            str(self.remote),
            f"{snap}:refs/cheese/snapshots/{TASK}",
            cwd=self.worktree,
        )


@pytest.fixture
def machine(tmp_path: Path) -> Machine:
    return Machine(tmp_path)


def test_save_confirms_the_work_reached_the_remote(machine: Machine) -> None:
    machine.commit("step.py", "print(1)\n")

    out = ran(machine.run("save"))

    assert machine.remote_tip(f"refs/heads/{BRANCH}") == machine.head()
    assert "就是" in out
    # 打出来的恢复命令得能直接跑：`cheese worktree` 只认完整 UUID。
    assert f"cheese worktree {TASK}" in out


def test_save_does_not_put_unfinished_work_on_the_task_branch(
    machine: Machine,
) -> None:
    """备份可以带走未提交的改动，PR 不能。"""
    machine.commit("step.py", "print(1)\n")
    head_before = machine.head()
    (machine.worktree / "step.py").write_text("print(2)\n")
    (machine.worktree / "scratch.txt").write_text("half done\n")

    ran(machine.run("save"))

    assert machine.head() == head_before
    assert machine.remote_tip(f"refs/heads/{BRANCH}") == head_before
    status = git("status", "--porcelain", cwd=machine.worktree)
    assert "scratch.txt" in status


def test_save_refuses_when_the_push_did_not_land(machine: Machine) -> None:
    """故意违规：同步命令跑过了，远端却没有这个提交，不能报「存好了」。"""
    machine.commit("step.py", "print(1)\n")

    result = machine.run("save", FAKE_SYNC_NOOP="1")

    refused(result, 3, "没能确认", f"cheese worktree {TASK}")
    assert machine.remote_tip(f"refs/heads/{BRANCH}") == ""


def test_save_refuses_when_the_remote_cannot_be_reached(
    machine: Machine,
) -> None:
    """故意违规：取不到远端时不能把「查不到」说成「没有」，得说清为什么。"""
    machine.commit("step.py", "print(1)\n")
    git(
        "remote",
        "set-url",
        "origin",
        str(machine.tmp / "not-a-repo.git"),
        cwd=machine.worktree,
    )

    result = machine.run("save")

    refused(result, 3, "取不到远端")
    assert "远端 refs/heads" not in result.stdout


def test_save_refuses_off_a_task_branch(machine: Machine) -> None:
    """故意违规：不在任务分支上，脚本不该猜是哪条任务。"""
    git("switch", "-q", "main", cwd=machine.worktree)

    result = machine.run("save")

    refused(result, 2, "任务分支", "cheese worktree")


def test_save_refuses_on_a_detached_head(machine: Machine) -> None:
    machine.commit("step.py", "print(1)\n")
    git("checkout", "-q", "--detach", cwd=machine.worktree)

    refused(machine.run("save"), 2, "分离头指针")


def test_save_names_what_a_machine_swap_would_take_with_it(
    machine: Machine,
) -> None:
    notes = machine.home / "room" / "notes.md"
    notes.write_text("研究笔记\n")
    machine.commit("step.py", "print(1)\n")

    result = machine.run("save")

    ran(result)
    assert str(notes) in result.stderr
    assert "任务目录以外" in result.stderr


def test_save_keeps_the_advisory_list_out_of_stdout(machine: Machine) -> None:
    """读 stdout 的人（和脚本）要的是「到没到远端」，别混进一列文件名。"""
    (machine.home / "room" / "notes.md").write_text("研究笔记\n")
    machine.commit("step.py", "print(1)\n")

    out = ran(machine.run("save"))

    assert "就是" in out
    assert "notes.md" not in out


def test_save_does_not_scan_shared_tmp(machine: Machine) -> None:
    """故意违规：把 /tmp 一起扫进来，别的进程留下的文件就把提醒挤没了。"""
    stray = Path("/tmp") / f"worktree-snapshot-stray-{os.getpid()}.txt"
    stray.write_text("别的进程的东西\n")
    try:
        machine.commit("step.py", "print(1)\n")

        result = machine.run("save")

        ran(result)
        assert str(stray) not in result.stderr
    finally:
        stray.unlink(missing_ok=True)


def test_the_machine_is_wiped_and_the_work_comes_back(machine: Machine) -> None:
    """工作机回收：整个目录删掉，从远端接回来，未提交的东西也在。"""
    machine.commit("step.py", "print(1)\n")
    (machine.worktree / "step.py").write_text("print(2)\n")
    (machine.worktree / "scratch.txt").write_text("half done\n")
    ran(machine.run("save"))

    machine.wipe()

    out = ran(machine.run("restore", "--task", TASK))

    assert str(machine.worktree) in out
    assert f"分支 {BRANCH}" in out
    assert (machine.worktree / "scratch.txt").read_text() == "half done\n"
    assert (machine.worktree / "step.py").read_text() == "print(2)\n"
    assert git("status", "--porcelain", cwd=machine.worktree) != ""


def test_restore_accepts_commits_the_snapshot_put_back_on_the_branch(
    machine: Machine,
) -> None:
    """恢复会把备份里没推上去的提交快进回分支：本地比远端多是正常的。"""
    machine.commit("step.py", "print(1)\n")
    ran(machine.run("save"))
    machine.commit("step.py", "print(2)\n")
    machine.snapshot_without_pushing_the_branch()
    remote_tip = machine.remote_tip(f"refs/heads/{BRANCH}")

    machine.wipe()

    out = ran(machine.run("restore", "--task", TASK))

    assert machine.remote_tip(f"refs/heads/{BRANCH}") == remote_tip
    assert "还没推到远端" in out
    assert (machine.worktree / "step.py").read_text() == "print(2)\n"


def test_restore_refuses_a_state_that_lags_the_remote(machine: Machine) -> None:
    """故意违规：接回来的是个旧状态，这时不能在目录里接着干活。"""
    machine.commit("step.py", "print(1)\n")
    ran(machine.run("save"))
    machine.commit("step.py", "print(2)\n")
    ran(machine.run("save"))

    machine.wipe()

    result = machine.run("restore", "--task", TASK, FAKE_WORKTREE_STALE="1")

    refused(result, 3, "对不上")


def test_restore_refuses_a_short_task_id(machine: Machine) -> None:
    """故意违规：分支名里那 8 位不是任务 id，拿它去恢复会被 CLI 当场拒掉。"""
    machine.commit("step.py", "print(1)\n")
    ran(machine.run("save"))
    machine.wipe()

    refused(machine.run("restore", "--task", SHORT), 2, "完整")


def test_restore_reports_failure_instead_of_pretending(machine: Machine) -> None:
    machine.commit("step.py", "print(1)\n")
    machine.wipe()

    # 远端根本没有这条任务：假 cheese 的 clone/branch 会失败。
    result = machine.run("restore", "--task", "11111111-2222-3333-4444-555555555555")

    assert result.returncode != 0
    assert "失败" in result.stderr


def test_restore_reads_the_worktree_out_of_the_cli_output(
    machine: Machine,
) -> None:
    machine.commit("step.py", "print(1)\n")
    ran(machine.run("save"))
    machine.wipe()

    out = ran(machine.run("restore", "--task", TASK, FAKE_WORKTREE_CHATTER="1"))

    assert f"工作目录 {machine.worktree}" in out
