#!/usr/bin/env python3
"""自测 `.claude` 之外那个随技能下发的 `worktree-snapshot.sh`。

这个脚本是「跑会话的机器被回收」这条路上的最短路，它自己不做快照：快照和恢复是
平台的事（`cheese sync` / `cheese worktree`）。所以这里用一个假的 `cheese` 按平台
文档里那份契约演一遍，只断言脚本自己的行为：

  * 什么时候说「核对通过」，什么时候不许说（远端没有那个提交时退出 3）；
  * 不在任务目录里、分离头指针时拒判（退出 2），并把该跑什么说清楚；
  * 未提交的改动不许被它推进任务分支——那是 PR 的内容，不是备份的内容；
  * 换机模拟：整个工作目录删掉，从远端接回来，未提交的东西也在。

假 `cheese` 只实现两个子命令的对外行为，不读脚本的实现。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "backend" / "sandbox" / "skills" / "cheese" / "scripts" / "worktree-snapshot.sh"

TASK = "61525614"
BRANCH = f"task/{TASK}"

GIT_ENV = {
    "GIT_AUTHOR_NAME": "snapshot test",
    "GIT_AUTHOR_EMAIL": "snapshot@test.invalid",
    "GIT_COMMITTER_NAME": "snapshot test",
    "GIT_COMMITTER_EMAIL": "snapshot@test.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_TERMINAL_PROMPT": "0",
}

FAKE_CHEESE = r"""#!/usr/bin/env bash
# 按 docs/manual/dev/tasks.md 的备份/恢复契约演一遍，只到脚本用得到的那几步。
set -euo pipefail

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
    git push -q "$FAKE_REMOTE" "$snap:refs/cheese/snapshots/${branch#task/}"
    ;;
  worktree)
    id=${2:?}
    dir=${FAKE_WORKTREE_DIR:?}
    if [ ! -d "$dir/.git" ]; then
      git clone -q "$FAKE_REMOTE" "$dir"
    fi
    cd "$dir"
    git checkout -q -B "task/$id" "origin/task/$id"
    if snap=$(git ls-remote origin "refs/cheese/snapshots/$id" | cut -f1) && [ -n "$snap" ]; then
      git fetch -q origin "refs/cheese/snapshots/$id"
      git checkout -q FETCH_HEAD -- .
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


class SnapshotCase(unittest.TestCase):
    """一个临时远端 + 一个任务工作目录，每条用例自己起一套。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="worktree-snapshot-")
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "home"
        (self.home / "room").mkdir(parents=True)
        self.remote = self.tmp / "origin.git"
        self.worktree = self.tmp / "work"

        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        fake = self.bin / "cheese"
        fake.write_text(FAKE_CHEESE)
        fake.chmod(0o755)

        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.worktree)], check=True)
        (self.worktree / "README.md").write_text("base\n")
        git("add", "README.md", cwd=self.worktree)
        git("commit", "-q", "-m", "base", cwd=self.worktree)
        git("remote", "add", "origin", str(self.remote), cwd=self.worktree)
        git("push", "-q", "-u", "origin", "main", cwd=self.worktree)
        git("switch", "-q", "-c", BRANCH, cwd=self.worktree)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_script(
        self, *args: str, cwd: Path | None = None, **extra_env: str
    ) -> subprocess.CompletedProcess[str]:
        where = cwd or (self.worktree if self.worktree.is_dir() else self.tmp)
        env = {
            **os.environ,
            **GIT_ENV,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "HOME": str(self.home),
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

    def remote_tip(self, ref: str) -> str:
        out = subprocess.run(
            ["git", "ls-remote", str(self.remote), ref],
            env={**os.environ, **GIT_ENV},
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return out.split("\t")[0] if out else ""

    def commit(self, name: str, body: str) -> None:
        (self.worktree / name).write_text(body)
        git("add", name, cwd=self.worktree)
        git("commit", "-q", "-m", f"add {name}", cwd=self.worktree)


class TestSave(SnapshotCase):
    def test_save_confirms_the_work_reached_the_remote(self) -> None:
        self.commit("step.py", "print(1)\n")

        result = self.run_script("save")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.remote_tip(f"refs/heads/{BRANCH}"), git("rev-parse", "HEAD", cwd=self.worktree))
        self.assertIn("就是", result.stdout)
        self.assertIn(f"cheese worktree {TASK}", result.stdout)

    def test_save_does_not_put_unfinished_work_on_the_task_branch(self) -> None:
        """备份可以带走未提交的改动，PR 不能。"""
        self.commit("step.py", "print(1)\n")
        head_before = git("rev-parse", "HEAD", cwd=self.worktree)
        (self.worktree / "step.py").write_text("print(2)\n")
        (self.worktree / "scratch.txt").write_text("half done\n")

        result = self.run_script("save")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.worktree), head_before)
        self.assertEqual(self.remote_tip(f"refs/heads/{BRANCH}"), head_before)
        self.assertIn("scratch.txt", git("status", "--porcelain", cwd=self.worktree))

    def test_save_refuses_when_the_push_did_not_land(self) -> None:
        """故意违规：同步命令跑过了，远端却没有这个提交，不能报「存好了」。"""
        self.commit("step.py", "print(1)\n")

        result = self.run_script("save", FAKE_SYNC_NOOP="1")

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("没能确认", result.stderr)
        self.assertIn(f"cheese worktree {TASK}", result.stderr)
        self.assertEqual(self.remote_tip(f"refs/heads/{BRANCH}"), "")

    def test_save_refuses_when_the_remote_cannot_be_reached(self) -> None:
        """故意违规：取不到远端时不能把「查不到」说成「没有」，得说清为什么。"""
        self.commit("step.py", "print(1)\n")
        git("remote", "set-url", "origin", str(self.tmp / "not-a-repo.git"), cwd=self.worktree)

        result = self.run_script("save")

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("取不到远端", result.stderr)
        self.assertNotIn("远端 refs/heads", result.stdout)

    def test_save_refuses_off_a_task_branch(self) -> None:
        """故意违规：不在任务分支上，脚本不该猜是哪条任务。"""
        git("switch", "-q", "main", cwd=self.worktree)

        result = self.run_script("save")

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("任务分支", result.stderr)
        self.assertIn("cheese worktree", result.stderr)

    def test_save_refuses_on_a_detached_head(self) -> None:
        self.commit("step.py", "print(1)\n")
        git("checkout", "-q", "--detach", cwd=self.worktree)

        result = self.run_script("save")

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("分离头指针", result.stderr)

    def test_save_names_what_a_machine_swap_would_take_with_it(self) -> None:
        (self.home / "room" / "notes.md").write_text("研究笔记\n")
        self.commit("step.py", "print(1)\n")

        result = self.run_script("save")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(self.home / "room" / "notes.md"), result.stdout)
        self.assertIn("任务目录以外", result.stdout)


class TestRestore(SnapshotCase):
    def test_the_machine_is_wiped_and_the_work_comes_back(self) -> None:
        """工作机回收：整个目录删掉，从远端接回来，未提交的东西也在。"""
        self.commit("step.py", "print(1)\n")
        (self.worktree / "step.py").write_text("print(2)\n")
        (self.worktree / "scratch.txt").write_text("half done\n")
        self.assertEqual(self.run_script("save").returncode, 0)

        shutil.rmtree(self.worktree)

        result = self.run_script("restore", "--task", TASK)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(self.worktree), result.stdout)
        self.assertIn(f"分支 {BRANCH}", result.stdout)
        self.assertEqual((self.worktree / "scratch.txt").read_text(), "half done\n")
        self.assertEqual((self.worktree / "step.py").read_text(), "print(2)\n")
        self.assertGreater(int(git("status", "--porcelain", cwd=self.worktree).count("\n") or 0), 0)

    def test_restore_reports_failure_instead_of_pretending(self) -> None:
        self.commit("step.py", "print(1)\n")
        shutil.rmtree(self.worktree)

        # 远端根本没有这条任务：假 cheese 的 clone/branch 会失败。
        result = self.run_script("restore", "--task", "no-such-task")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("失败", result.stderr)


if __name__ == "__main__":
    unittest.main()
