"""一块长命的屏幕，跨过一次真实的 squash 合并，继续交付下一批。

The screen outlives the batch. It clones once, and its environment — including
`CHEESE_GIT_BRANCH` — is fixed at that moment; the room then delivers, the batch
is SQUASHED into main, and the next batch opens on a new branch. Two things break
if nothing is done, and neither of them can be worked out from inside the clone:

- it keeps pushing onto the branch that already landed (`git push` succeeds, the
  hook reports ok, and the work sits on a branch main can never reach);
- its HEAD is still built on the delivered commits, so pushing that onto the new
  branch re-delivers the previous batch — the PR's three-dot diff shows it again
  and the squash commit claims it again.

Ancestry cannot answer either question: **a squash commit is not a descendant of
the branch it squashed**, so `merge-base --is-ancestor` says "no" for a batch
that is fully delivered and "no" for one that never was. So the platform states
both facts (`on_delivered`, `base_sha`) and the device acts only on them.

Everything here is a real repository and the real generated script. The one thing
that is faked is the platform's answer — which is the point: these pin what the
device does with a given answer.
"""

import json
import os
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.domain.agent.harness.claude_code.device_launch import build_launch_script


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, f"git {' '.join(args)}: {done.stderr}"
    return done.stdout


def _sync_body() -> str:
    return build_launch_script(sync_on_stop=True).split("'SYNC'")[1].split("SYNC")[0]


class _Platform:
    """The platform's answer to「这批活现在写哪条分支」, over a real socket."""

    def __init__(self) -> None:
        self.payload: dict = {}
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler's own spelling
                body = json.dumps(outer.payload).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/branch"

    def stop(self) -> None:
        self._server.shutdown()


def _platform_repo(root: Path) -> Path:
    """A bare repo with a main branch — what the device clones and pushes to."""
    seed = root / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main", ".")
    _git(seed, "config", "user.email", "p@z")
    _git(seed, "config", "user.name", "platform")
    (seed / "README.md").write_text("the project\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "chore: initialize")
    bare = root / "platform.git"
    _git(root, "clone", "-q", "--bare", str(seed), str(bare))
    return bare


def _device_clone(root: Path, remote: Path, branch: str) -> Path:
    work = root / "work"
    _git(root, "clone", "-q", str(remote), str(work))
    _git(work, "config", "user.email", "c@z")
    _git(work, "config", "user.name", "芝士")
    _git(work, "checkout", "-q", "-b", branch)
    return work


def _turn(work: Path, sync: Path, platform: _Platform, remote: Path, log: Path) -> str:
    bindir = work.parent / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "cheese-hook").write_text(f'#!/bin/sh\ncat >> "{log}"\n')
    (bindir / "cheese-hook").chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "CHEESE_GIT_REMOTE": str(remote),
        # 屏幕启动那一刻冻住的那个值。跨过采纳之后它是错的，脚本不许用它。
        "CHEESE_GIT_BRANCH": "topic/one",
        "CHEESE_BRANCH_URL": platform.url,
        "CHEESE_WORK": str(work),
    }
    done = subprocess.run(
        ["sh", str(sync)], env=env, capture_output=True, text=True, timeout=120
    )
    assert done.returncode == 0, done.stderr
    return log.read_text()


def _squash_into_main(bare: Path, root: Path, branch: str) -> str:
    """真的 squash 合并：main 拿到内容，而那条分支**不是** main 的祖先。"""
    staging = root / "staging"
    _git(root, "clone", "-q", str(bare), str(staging))
    _git(staging, "config", "user.email", "p@z")
    _git(staging, "config", "user.name", "platform")
    _git(staging, "checkout", "-q", "main")
    _git(staging, "merge", "--squash", f"origin/{branch}")
    _git(staging, "commit", "-qm", "feat: batch one (#1)")
    _git(staging, "push", "-q", "origin", "main")
    return _git(staging, "rev-parse", "HEAD").strip()


def _refs(bare: Path) -> str:
    return _git(bare, "for-each-ref", "--format=%(refname)")


def _pr_would_show(bare: Path, base: str, head: str) -> list[str]:
    """What a reviewer opening the PR would see — GitHub's THREE-dot diff.

    Two dots would not catch this bug: it compares the two trees, and a batch
    that was re-delivered has content identical to what the squash already put
    on main, so it shows nothing. Three dots is diff(merge-base, head), which is
    exactly where a re-delivered batch reappears.
    """
    out = _git(bare, "diff", "--name-only", f"{base}...{head}")
    return sorted(x for x in out.splitlines() if x)


def test_the_next_batch_carries_only_its_own_changes_onto_the_new_branch():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bare = _platform_repo(root)
        work = _device_clone(root, bare, "topic/one")
        sync = root / "cheese-sync"
        sync.write_text(_sync_body())
        log = root / "hook.log"
        platform = _Platform()
        try:
            # 第一批：写、提交、同步。
            platform.payload = {"branch": "topic/one", "on_delivered": False}
            (work / "one.txt").write_text("batch one\n")
            _git(work, "add", "-A")
            _git(work, "commit", "-qm", "feat: batch one")
            assert '"status":"ok"' in _turn(work, sync, platform, bare, log)
            assert "refs/heads/topic/one" in _refs(bare)

            # 真的 squash 进 main。分支从此不是 main 的祖先。
            main_sha = _squash_into_main(bare, root, "topic/one")
            delivered_tip = _git(work, "rev-parse", "HEAD").strip()
            assert (
                subprocess.run(
                    ["git", "merge-base", "--is-ancestor", delivered_tip, main_sha],
                    cwd=bare,
                    capture_output=True,
                ).returncode
                != 0
            ), "squash 之后那条分支不该是 main 的祖先，否则这个用例证明不了什么"

            # 第二批：**同一个 clone**，HEAD 还停在上一批的提交上。
            platform.payload = {
                "branch": "topic/two",
                "base": "main",
                "base_sha": main_sha,
                "on_delivered": True,
            }
            (work / "two.txt").write_text("batch two\n")
            _git(work, "add", "-A")
            _git(work, "commit", "-qm", "feat: batch two")
            (work / "scratch.txt").write_text("还没提交的东西\n")
            before_head = _git(work, "rev-parse", "HEAD").strip()
            before_status = _git(work, "status", "--porcelain")

            reported = _turn(work, sync, platform, bare, log)

            assert '"status":"ok"' in reported, reported
            assert "refs/heads/topic/two" in _refs(bare)
            # 第二批的 PR 里只有第二批的改动。
            assert _pr_would_show(bare, "main", "topic/two") == ["two.txt"]
            # 而第一批那条分支上的东西没有被再交付一次。
            assert "one.txt" not in _pr_would_show(bare, "main", "topic/two")
            # 分身正在看的东西一个字节都没动：HEAD 没挪，未提交的改动还在。
            assert _git(work, "rev-parse", "HEAD").strip() == before_head
            assert _git(work, "status", "--porcelain") == before_status
            assert (work / "scratch.txt").read_text() == "还没提交的东西\n"
            # 未提交的内容照样离开了机器。
            assert "refs/cheese/snapshots/topic/two" in _refs(bare)
        finally:
            platform.stop()


def test_a_conflict_carrying_the_batch_over_is_reported_and_nothing_is_lost():
    """接不上去就说接不上去。悄悄推一个「尽力而为」的结果，等于把一次没人看过的
    三方合并当成分身的交付。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bare = _platform_repo(root)
        work = _device_clone(root, bare, "topic/one")
        sync = root / "cheese-sync"
        sync.write_text(_sync_body())
        log = root / "hook.log"
        platform = _Platform()
        try:
            platform.payload = {"branch": "topic/one", "on_delivered": False}
            (work / "shared.txt").write_text("the agent's version\n")
            _git(work, "add", "-A")
            _git(work, "commit", "-qm", "feat: batch one")
            _turn(work, sync, platform, bare, log)
            _squash_into_main(bare, root, "topic/one")

            # 别人在 main 上把同一行改成了别的东西。
            staging = root / "staging2"
            _git(root, "clone", "-q", str(bare), str(staging))
            _git(staging, "config", "user.email", "p@z")
            _git(staging, "config", "user.name", "platform")
            (staging / "shared.txt").write_text("somebody else's version\n")
            _git(staging, "commit", "-qam", "fix: somebody else's version")
            _git(staging, "push", "-q", "origin", "main")
            main_sha = _git(staging, "rev-parse", "HEAD").strip()

            platform.payload = {
                "branch": "topic/two",
                "base": "main",
                "base_sha": main_sha,
                "on_delivered": True,
            }
            (work / "shared.txt").write_text("the agent's next version\n")
            _git(work, "commit", "-qam", "feat: batch two")
            (work / "scratch.txt").write_text("还没提交的东西\n")
            log.write_text("")

            reported = _turn(work, sync, platform, bare, log)

            assert '"status":"failed"' in reported, reported
            assert "conflict" in reported, reported
            # 没有把一个没人看过的合并结果当成交付推出去。
            assert "refs/heads/topic/two" not in _refs(bare)
            # 但内容全在：未提交的东西进了快照 ref，工作区原封不动。
            assert "refs/cheese/snapshots/topic/two" in _refs(bare)
            assert (work / "scratch.txt").read_text() == "还没提交的东西\n"
            assert (work / "shared.txt").read_text() == "the agent's next version\n"
        finally:
            platform.stop()
