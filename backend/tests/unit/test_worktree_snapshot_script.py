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
  * 分支一次都没推到远端（东西只在平台快照里）时，恢复要成功，并让人先跑 save；
  * 平台把快照放到独立目录、没套进工作区时，不许说「接回来了」；
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
    if [ "${FAKE_SYNC_FAIL:-}" = 1 ]; then
      echo "[cheese] 同步失败：远端拒绝了这次推送" >&2
      exit 1
    fi
    [ "${FAKE_SYNC_NOOP:-}" = 1 ] && exit 0
    root=$(git rev-parse --show-toplevel)
    cd "$root"
    branch=$(git symbolic-ref --quiet --short HEAD)
    if [ "${FAKE_SYNC_SWITCHES:-}" = 1 ]; then
      # 多步任务的下一步换了分支（task/<id>-2）：平台把目录跟过去，推的是新那条。
      branch="task/${CHEESE_TOPIC%%-*}-2"
      git switch -q -C "$branch"
    elif [ -n "${FAKE_SYNC_SWITCHES_TO:-}" ]; then
      # 同一步把目录带到了别处（不在任务分支上）。
      branch=$FAKE_SYNC_SWITCHES_TO
      git switch -q -C "$branch"
    fi
    head=$(git rev-parse HEAD)
    idx="$root/.git/fake-snapshot-index"
    rm -f "$idx"
    GIT_INDEX_FILE="$idx" git read-tree HEAD
    GIT_INDEX_FILE="$idx" git add -A
    tree=$(GIT_INDEX_FILE="$idx" git write-tree)
    rm -f "$idx"
    snap=$(git commit-tree "$tree" -p "$head" -m "cheese snapshot")
    git push -q "$FAKE_REMOTE" "$head:refs/heads/$branch"
    [ -n "${FAKE_SYNC_MARK:-}" ] && : > "$FAKE_SYNC_MARK"
    # 平台每备份一次就做一个新的快照提交，所以这个 ref 是往前顶的（真实现是本
    # 地用 update-ref + 传 bundle，这里用 --force 推同一个 ref 代替）。
    git push -q --force "$FAKE_REMOTE" "$snap:$(snapshot_ref)"
    if [ "${FAKE_SYNC_SET_ASIDE:-}" = 1 ]; then
      # 重试标记还在时 sync 会再试一次恢复；平台仍然拒绝套用，把快照放到独立
      # 目录，然后照样把当前检出备份成新的一版（_restore_snapshot 返回真）。
      mkdir -p "$HOME/.cheese/recovered/${CHEESE_TOPIC}-0123456789ab"
    fi
    ;;
  worktree)
    id=${2:?}
    # 真 CLI 第一件事就是 uuid.UUID(task_id)，短 id 在这里当场失败；大写它照收，
    # 归一化成小写——任务目录、recovered 下的目录、分支名都是小写。
    uuid='[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
    printf '%s' "$id" | grep -Eqx "$uuid" || {
      echo "fake cheese: badly formed UUID string: $id" >&2
      exit 1
    }
    id=$(printf '%s' "$id" | tr 'A-F' 'a-f')
    short=${id%%-*}
    dir=${FAKE_WORKTREE_DIR:?}
    if [ "${FAKE_WORKTREE_FAIL:-}" = 1 ]; then
      echo "[cheese] 任务 $id 取不下来：平台说这条任务不在这台机器上" >&2
      exit 1
    fi
    [ -d "$dir/.git" ] || git clone -q "$FAKE_REMOTE" "$dir"
    cd "$dir"
    # _task_worktree：远端有这条分支就接它，没有就从 base 建一条本地分支——推送
    # 失败或中断过的任务正是后一种，这是它该恢复的场景，不是 CLI 的失败。
    if git ls-remote --heads origin "task/$short" | grep -q .; then
      git checkout -q -B "task/$short" "origin/task/$short"
    else
      git checkout -q -B "task/$short" origin/main
    fi
    if [ "${FAKE_WORKTREE_CHATTER:-}" = 1 ]; then
      echo "[cheese] 新环境，正在接回任务 $id 的备份" >&2
    fi
    if [ "${FAKE_WORKTREE_SET_ASIDE:-}" = 1 ]; then
      # _apply_snapshot 拒绝套用时：退出码仍是 0，快照放到独立目录，只写 stderr。
      aside="$HOME/.cheese/recovered/$id-0123456789ab"
      mkdir -p "$aside"
      echo "[cheese] 任务 $id 在平台上有一份快照，但分支已经前进，" >&2
      echo "没有自动套用，免得覆盖这里的东西。快照原样放在独立目录 $aside。" >&2
      pwd
      exit 0
    fi
    if [ "${FAKE_WORKTREE_RESTORE_FAILED:-}" = 1 ]; then
      # _restore_snapshot 的半途失败：留着重试标记，只往 stderr 说一句，退出 0。
      : > "$(git rev-parse --git-path cheese-restore-pending)"
      echo "[cheese] 任务 $id：没能从平台快照恢复上次没推送的工作。" >&2
      pwd
      exit 0
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
    if [ "${FAKE_WORKTREE_UNREACHABLE:-}" = 1 ]; then
      # 目录建好了，远端却连不上：恢复本身成功，核对时取不到远端。
      git remote set-url origin "$FAKE_REMOTE-missing"
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

    def another_checkout_pushes_further(self) -> str:
        """另一个 checkout 把同一条任务分支推得更远——平台允许这种写入者。

        它的 HEAD 是本地 HEAD 的子提交：本地这个提交确实已经在远端上了。
        """
        other = self.tmp / "other-checkout"
        subprocess.run(
            ["git", "clone", "-q", str(self.remote), str(other)],
            env=_env(),
            check=True,
            capture_output=True,
        )
        git("switch", "-q", "-C", BRANCH, f"origin/{BRANCH}", cwd=other)
        (other / "later.py").write_text("print(3)\n")
        git("add", "later.py", cwd=other)
        git("commit", "-q", "-m", "later", cwd=other)
        git("push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}", cwd=other)
        return git("rev-parse", "HEAD", cwd=other)


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


def test_save_reports_a_sync_that_fails(machine: Machine) -> None:
    """同步自己失败：原样把 CLI 说的话给出来，不能顺水推舟说「存好了」。"""
    machine.commit("step.py", "print(1)\n")

    result = machine.run("save", FAKE_SYNC_FAIL="1")

    refused(result, 1, "cheese sync 失败")
    # CLI 说的原话原样打出来，别让跑的人只看到一句概述。
    assert "远端拒绝了这次推送" in result.stdout
    assert "换机后接回来" not in result.stdout


def test_save_accepts_a_branch_another_checkout_pushed_further(
    machine: Machine,
) -> None:
    """远端走得更远是正常的：本地这个提交在远端的历史里，就算核对通过。

    平台自己的 `_deliver_task_branch` 在这种情况下直接成功返回（不推、也不报
    错），所以「远端顶端恰好等于本地 HEAD」会被判成一次假失败。
    """
    machine.commit("step.py", "print(1)\n")
    ran(machine.run("save"))
    further = machine.another_checkout_pushes_further()

    out = ran(machine.run("save", FAKE_SYNC_NOOP="1"))

    assert machine.remote_tip(f"refs/heads/{BRANCH}") == further
    assert "包含本地" in out


def test_save_checks_the_branch_the_sync_moved_to(machine: Machine) -> None:
    """多步任务的下一步换了分支，`cheese sync` 会把目录跟过去。

    跟着旧名字核对，会在推成功之后报「远端没有这个提交」。
    """
    machine.commit("step.py", "print(1)\n")

    out = ran(machine.run("save", FAKE_SYNC_SWITCHES="1"))

    assert f"refs/heads/{BRANCH}-2" in out
    assert machine.remote_tip(f"refs/heads/{BRANCH}-2") == machine.head()


def test_save_says_the_set_aside_backup_will_not_be_the_latest_one(
    machine: Machine,
) -> None:
    """恢复没接回来时，下一次同步会让当前检出成为最近一版备份。

    平台自己就是这么设计的（分支前进时快照不被套进工作区，当前检出接着备份
    下去，见 test_a_replaced_sandbox_comes_back_with_its_work），所以 save 不
    拦这次同步——拦也拦不住平台自己的 hook——但必须说清那份活不在备份里，
    不能让它读成「换机后都接得回来」。
    """
    machine.commit("step.py", "print(1)\n")
    ran(machine.run("save"))
    machine.wipe()
    refused(
        machine.run("restore", "--task", TASK, FAKE_WORKTREE_SET_ASIDE="1"),
        3,
        ".cheese/recovered",
    )
    # 有新的工作要存：这时同步确实会顶掉平台那份，只能提醒。
    machine.commit("step2.py", "print(2)\n")
    mark = machine.tmp / "sync-ran"

    result = machine.run("save", FAKE_SYNC_MARK=str(mark))

    out = ran(result)
    assert mark.exists(), "这次同步没有跑"
    assert ".cheese/recovered" in result.stderr
    assert "那份快照里的改动不会" in result.stderr
    # 同步前就看到的目录，不能又说成是同步时放下的。
    assert "同步时平台把一份快照原样放到了" not in result.stderr
    assert "要自己取" in out


def test_save_says_when_the_snapshot_is_set_aside_during_the_sync(
    machine: Machine,
) -> None:
    """同步过程中平台才把快照放到独立目录：本次备份会顶掉它，得说出来。

    提交确实推上去了，所以不是推送失败；但那份快照里的改动不在备份里。
    """
    machine.commit("step.py", "print(1)\n")

    result = machine.run("save", FAKE_SYNC_SET_ASIDE="1")

    out = ran(result)
    assert "同步时平台把一份快照原样放到了" in result.stderr
    assert "要自己取" in out
    assert machine.remote_tip(f"refs/heads/{BRANCH}") == machine.head()


def test_save_refuses_a_sync_that_leaves_the_task_branch(machine: Machine) -> None:
    """故意违规：同步把目录带到别处，推上去的就不是任务分支了。"""
    machine.commit("step.py", "print(1)\n")

    refused(machine.run("save", FAKE_SYNC_SWITCHES_TO="wip"), 2, "不是任务分支")


def test_save_refuses_a_task_option(machine: Machine) -> None:
    """故意违规：save 只认当前目录，给了别的任务 id 时不能照着它核对。"""
    machine.commit("step.py", "print(1)\n")

    refused(machine.run("save", "--task", TASK), 2, "save 不接受 --task")


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
    # 平台每次都在动的运行时状态不是谁的活；列出来只会盖住上面那条。
    runtime = machine.home / ".cheese-environment" / "status.json"
    runtime.parent.mkdir()
    runtime.write_text("{}\n")
    machine.commit("step.py", "print(1)\n")

    result = machine.run("save")

    ran(result)
    assert str(notes) in result.stderr
    assert "任务目录以外" in result.stderr
    assert str(runtime) not in result.stderr


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

        # 列表要长到不会被截断：/tmp 里别的文件比它排得靠前，截断会把这条规则
        # 的用例变成「碰巧没列出来」。
        result = machine.run("save", MAX_LISTED="1000")

        ran(result)
        assert str(stray) not in result.stderr
    finally:
        stray.unlink(missing_ok=True)


def test_save_still_passes_when_the_advisory_scan_fails(machine: Machine) -> None:
    """提醒扫不动（权限、没有 $HOME）不能把已经核对过的成功改成失败。"""
    fake_find = machine.bin / "find"
    fake_find.write_text("#!/usr/bin/env bash\nexit 1\n")
    fake_find.chmod(0o755)
    machine.commit("step.py", "print(1)\n")

    out = ran(machine.run("save"))

    assert f"cheese worktree {TASK}" in out


def test_save_keeps_the_exit_code_when_the_advisory_list_is_long(
    machine: Machine,
) -> None:
    """列表长过管道缓冲时，截断的那一头早退会把整条管线带成 141。

    `set -o pipefail` 下这个退出码就是脚本的退出码：核对明明通过了，却报失败。
    """
    room = machine.home / "room"
    for i in range(1500):
        (room / f"note-{i:05d}.txt").write_text("x\n")
    machine.commit("step.py", "print(1)\n")

    result = machine.run("save")

    ran(result)
    assert "个里只列了前" in result.stderr


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
    assert "工作区没提交的文件：2 个" in out


def test_restore_brings_back_a_branch_that_never_reached_the_remote(
    machine: Machine,
) -> None:
    """要救的正是这种：分支从没推上去，东西只在平台快照里。

    `cheese worktree` 会从 base 建一条本地分支、再把快照快进上去，所以远端仍然
    是空的——这不是「对不上」，是「还没推」。
    """
    machine.commit("step.py", "print(1)\n")
    (machine.worktree / "scratch.txt").write_text("half done\n")
    machine.snapshot_without_pushing_the_branch()
    assert machine.remote_tip(f"refs/heads/{BRANCH}") == ""

    machine.wipe()

    out = ran(machine.run("restore", "--task", TASK))

    assert (machine.worktree / "step.py").read_text() == "print(1)\n"
    assert (machine.worktree / "scratch.txt").read_text() == "half done\n"
    assert "还没有提交" in out
    assert "先跑 save" in out


def test_restore_refuses_when_the_backup_was_set_aside(
    machine: Machine,
) -> None:
    """故意违规：CLI 退出码是 0，但快照没套进工作区。

    平台这时把快照原样放到 ~/.cheese/recovered/<任务 id>-<快照>，只往 stderr
    写一句。分支和远端对得上，不代表未提交的活回来了。
    """
    machine.commit("step.py", "print(1)\n")
    ran(machine.run("save"))

    machine.wipe()

    result = machine.run("restore", "--task", TASK, FAKE_WORKTREE_SET_ASIDE="1")

    refused(result, 3, "没套进", ".cheese/recovered")
    assert "与远端一致" not in result.stdout


def test_save_keeps_naming_the_set_aside_after_a_second_scan_fails(
    machine: Machine,
) -> None:
    """同步前看到的那份快照，不会因为同步后的扫描失败就从输出里消失。"""
    machine.commit("step.py", "print(1)\n")
    ran(machine.run("save"))
    (machine.home / ".cheese" / "recovered" / f"{TASK}-0123456789ab").mkdir(
        parents=True
    )
    mark = machine.tmp / "sync-ran"
    real_find = shutil.which("find")
    fake_find = machine.bin / "find"
    fake_find.write_text(
        f'#!/usr/bin/env bash\n[ -e "{mark}" ] && exit 1\nexec "{real_find}" "$@"\n'
    )
    fake_find.chmod(0o755)
    # 有东西要存才走得到同步之后的那次扫描。
    machine.commit("step2.py", "print(2)\n")

    result = machine.run("save", FAKE_SYNC_MARK=str(mark))

    out = ran(result)
    assert "没法确认平台有没有在同步时" in result.stderr
    assert f"{TASK}-0123456789ab" in out
    assert "那份快照在" in out
    assert "要自己取" in out


def test_save_refuses_when_the_set_aside_backup_is_the_only_copy(
    machine: Machine,
) -> None:
    """故意违规：干净检出上跑 save 的唯一后果是把平台那份快照顶掉。

    平台自己的 turn-end 同步会跳过这种检出（_has_work_to_sync），只有 save
    里这条显式同步会照样备份；这一下就把恢复时没套进工作区的那份挤掉了。
    """
    machine.commit("step.py", "print(1)\n")
    ran(machine.run("save"))
    tip = machine.remote_tip(f"refs/heads/{BRANCH}")
    machine.wipe()
    refused(
        machine.run("restore", "--task", TASK, FAKE_WORKTREE_SET_ASIDE="1"),
        3,
        ".cheese/recovered",
    )
    mark = machine.tmp / "sync-ran"

    result = machine.run("save", FAKE_SYNC_MARK=str(mark))

    refused(result, 3, ".cheese/recovered", "顶掉")
    assert not mark.exists(), "没有东西要存时不该同步"
    assert machine.remote_tip(f"refs/heads/{BRANCH}") == tip
    assert "换机后接回来" not in result.stdout


def test_save_refuses_when_the_backup_directory_cannot_be_scanned(
    machine: Machine,
) -> None:
    """扫不动时不能同步：那目录里可能是唯一一份没备份的活。"""
    machine.commit("step.py", "print(1)\n")
    ran(machine.run("save"))
    (machine.home / ".cheese" / "recovered").mkdir()
    fake_find = machine.bin / "find"
    fake_find.write_text("#!/usr/bin/env bash\nexit 1\n")
    fake_find.chmod(0o755)
    mark = machine.tmp / "sync-ran"

    result = machine.run("save", FAKE_SYNC_MARK=str(mark))

    refused(result, 3, "扫描", "没法确认")
    assert not mark.exists(), "没法确认时不该同步"
    assert "换机后接回来" not in result.stdout


def test_restore_still_refuses_after_a_set_aside_the_run_before(
    machine: Machine,
) -> None:
    """独立目录会一直留着：第二次跑不能因为「这次没有新目录」就说接回来了。

    平台在明确不套用时已经把重试标记删掉了，所以第二次 `cheese worktree` 什么
    都不做；再说一次「接回来了」，人就会以为未提交的活已经在了。
    """
    machine.commit("step.py", "print(1)\n")
    ran(machine.run("save"))

    machine.wipe()

    first = machine.run("restore", "--task", TASK, FAKE_WORKTREE_SET_ASIDE="1")
    # 第二次平台什么都不做：目录是上一轮留下的，脚本仍要认出来。
    second = machine.run("restore", "--task", TASK)

    refused(first, 3, ".cheese/recovered")
    refused(second, 3, ".cheese/recovered")
    assert "与远端一致" not in second.stdout


def test_restore_finds_a_set_aside_backup_for_an_upper_case_id(
    machine: Machine,
) -> None:
    """CLI 认大写 id，平台建的目录却是小写：照原样匹配会漏掉那一份。

    漏掉的后果是「接回来了」——而实际未提交的活还躺在独立目录里。
    """
    machine.commit("step.py", "print(1)\n")
    ran(machine.run("save"))

    machine.wipe()

    result = machine.run("restore", "--task", TASK.upper(), FAKE_WORKTREE_SET_ASIDE="1")

    refused(result, 3, ".cheese/recovered")
    assert "与远端一致" not in result.stdout


def test_restore_refuses_when_the_backup_directory_cannot_be_scanned(
    machine: Machine,
) -> None:
    """扫不动不能当成「没有」：那目录里可能是唯一一份没备份的活。

    和远端取不到时一样，这时没法核对接回来的状态，不能报「与远端一致」。
    """
    machine.commit("step.py", "print(1)\n")
    ran(machine.run("save"))
    # 目录在，但扫不动（权限、IO、PATH 上的同名程序）。
    (machine.home / ".cheese" / "recovered").mkdir()
    fake_find = machine.bin / "find"
    fake_find.write_text("#!/usr/bin/env bash\nexit 1\n")
    fake_find.chmod(0o755)

    machine.wipe()

    result = machine.run("restore", "--task", TASK)

    refused(result, 3, "扫描", "没法确认")
    assert "与远端一致" not in result.stdout


def test_restore_refuses_when_the_platform_could_not_apply_the_snapshot(
    machine: Machine,
) -> None:
    """故意违规：套用半途失败时平台也 exit 0，只留下重试标记和一句 stderr。"""
    machine.commit("step.py", "print(1)\n")
    ran(machine.run("save"))

    machine.wipe()

    result = machine.run("restore", "--task", TASK, FAKE_WORKTREE_RESTORE_FAILED="1")

    refused(result, 3, "没能把最近的备份接回来")
    assert "与远端一致" not in result.stdout


def test_restore_refuses_when_the_remote_cannot_be_reached(
    machine: Machine,
) -> None:
    """目录接回来了，远端却连不上：没法核对的不能当成核对通过。"""
    machine.commit("step.py", "print(1)\n")
    ran(machine.run("save"))

    machine.wipe()

    result = machine.run("restore", "--task", TASK, FAKE_WORKTREE_UNREACHABLE="1")

    refused(result, 3, "取不到远端")
    assert "与远端一致" not in result.stdout
    # 提示里让用户把上面的输出报上来；那行得带着该跑的命令。
    assert "远端现在是什么：git ls-remote origin refs/heads/" in result.stderr


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

    result = machine.run(
        "restore",
        "--task",
        "11111111-2222-3333-4444-555555555555",
        FAKE_WORKTREE_FAIL="1",
    )

    assert result.returncode != 0
    assert "失败" in result.stderr
    assert "取不下来" in result.stderr


def test_restore_refuses_a_missing_task_value(machine: Machine) -> None:
    """故意违规：`--task` 后面没东西时静悄悄退出，跑的人不知道发生了什么。"""
    refused(machine.run("restore", "--task"), 2, "--task")
    # 空值会退回 CHEESE_TOPIC，可能把别的任务接回来。
    refused(machine.run("restore", "--task", ""), 2, "--task")


def test_refuses_two_subcommands(machine: Machine) -> None:
    """故意违规：`save restore` 按最后一个跑，等于没按人说的做。"""
    refused(machine.run("save", "restore"), 2, "一次只跑一个")


def test_restore_reads_the_worktree_out_of_the_cli_output(
    machine: Machine,
) -> None:
    machine.commit("step.py", "print(1)\n")
    ran(machine.run("save"))
    machine.wipe()

    out = ran(machine.run("restore", "--task", TASK, FAKE_WORKTREE_CHATTER="1"))

    assert f"工作目录 {machine.worktree}" in out
