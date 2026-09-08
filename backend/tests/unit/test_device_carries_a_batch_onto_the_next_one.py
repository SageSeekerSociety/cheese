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
    """One turn ending. Returns what the hook reported FOR THIS TURN.

    The log is truncated first, deliberately. Reading a cumulative log and
    asserting `"status":"ok"` is in it is the most convincing false green there
    is — the ok can be last turn's, and the assertion passes while this turn
    failed.
    """
    log.write_text("")
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


_STAGING = [0]


def _squash_into_main(bare: Path, root: Path, branch: str) -> str:
    """真的 squash 合并：main 拿到内容，而那条分支**不是** main 的祖先。"""
    _STAGING[0] += 1
    staging = root / f"staging-{_STAGING[0]}"
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
                "on_head": delivered_tip,
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
                "on_head": _git(work, "rev-parse", "HEAD").strip(),
            }
            (work / "shared.txt").write_text("the agent's next version\n")
            _git(work, "commit", "-qam", "feat: batch two")
            (work / "scratch.txt").write_text("还没提交的东西\n")

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


def _remote_commit(bare: Path, root: Path, branch: str, name: str) -> str:
    """别人往这条分支上推了一个提交 —— 另一个分身、平台的 push-fix、或者人。"""
    _STAGING[0] += 1
    other = root / f"other-{_STAGING[0]}"
    _git(root, "clone", "-q", str(bare), str(other))
    _git(other, "config", "user.email", "o@z")
    _git(other, "config", "user.name", "somebody else")
    _git(other, "checkout", "-q", "-B", branch, f"origin/{branch}")
    (other / name).write_text(f"{name}\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-qm", f"chore: {name}")
    _git(other, "push", "-q", "origin", branch)
    return _git(other, "rev-parse", "HEAD").strip()


def _tree_of(bare: Path, branch: str) -> list[str]:
    out = _git(bare, "ls-tree", "-r", "--name-only", branch)
    return sorted(x for x in out.splitlines() if x)


def test_a_commit_somebody_else_pushed_is_not_wiped_out_by_this_turn():
    """远端已经有别人的提交，这一轮的推送**必须被拒**，而不是把它抹掉。

    一条无条件的 `push -f` 会静默吃掉房间里另一个分身、平台的 `push-fix`、或者人
    手推上去的东西 —— 而且照样报 ok。丢数据已经够糟，报成功更糟：出事的人连出过
    事都不知道。
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bare = _platform_repo(root)
        work = _device_clone(root, bare, "topic/one")
        sync = root / "cheese-sync"
        sync.write_text(_sync_body())
        log = root / "hook.log"
        platform = _Platform()
        platform.payload = {"branch": "topic/one", "on_delivered": False}
        try:
            (work / "mine.txt").write_text("mine\n")
            _git(work, "add", "-A")
            _git(work, "commit", "-qm", "feat: mine")
            _turn(work, sync, platform, bare, log)

            _remote_commit(bare, root, "topic/one", "other.txt")
            (work / "local.txt").write_text("local\n")
            _git(work, "add", "-A")
            _git(work, "commit", "-qm", "feat: local")

            reported = _turn(work, sync, platform, bare, log)
        finally:
            platform.stop()

        assert "other.txt" in _tree_of(bare, "topic/one"), (
            "别人的提交被这一轮的推送抹掉了"
        )
        assert '"status":"failed"' in reported, reported


def test_a_remote_that_moved_after_the_lease_was_read_is_not_overwritten():
    """衔接那一次是这条脚本里唯一的改写，所以它带 lease —— 而 lease 比对的必须是
    **刚从远端读到的那个 SHA**，不是本地那份可能几小时没更新的 remote-tracking。

    屏障卡在脚本读 lease 的那一刻：读完之后、推送之前，远端又前进一步。时序是
    构造出来的，不靠跑量。
    """
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
            (work / "one.txt").write_text("batch one\n")
            _git(work, "add", "-A")
            _git(work, "commit", "-qm", "feat: batch one")
            _turn(work, sync, platform, bare, log)
            delivered_tip = _git(work, "rev-parse", "HEAD").strip()
            main_sha = _squash_into_main(bare, root, "topic/one")
            # 下一批的分支上已经有东西了（另一个分身先开工了）。
            _git(bare, "branch", "topic/two", "topic/one")

            platform.payload = {
                "branch": "topic/two",
                "base": "main",
                "base_sha": main_sha,
                "on_delivered": True,
                "on_head": delivered_tip,
            }
            (work / "two.txt").write_text("batch two\n")
            _git(work, "add", "-A")
            _git(work, "commit", "-qm", "feat: batch two")
            _race_on_ls_remote(root, work.parent / "bin", bare, "topic/two")

            reported = _turn(work, sync, platform, bare, log)
        finally:
            platform.stop()

        assert (root / "raced").exists(), "屏障没有生效：远端没有在读 lease 之后动过"
        assert '"status":"failed"' in reported, reported
        assert "raced.txt" in _tree_of(bare, "topic/two"), (
            "读到 lease 之后落到远端的提交被这次衔接抹掉了"
        )
        assert "two.txt" not in _tree_of(bare, "topic/two")


def _race_on_ls_remote(root: Path, bindir: Path, bare: Path, branch: str) -> None:
    """装一个 `git` 垫片：答完 `ls-remote` 之后，远端立刻前进一步。

    这正是要证明的那个窗口 —— 脚本读远端当前值来构造 lease，读完到推之间落下的
    任何提交都不许被覆盖。
    """
    real = subprocess.run(
        ["which", "git"], capture_output=True, text=True, check=True
    ).stdout.strip()
    racer = root / "race.sh"
    racer.write_text(
        "#!/bin/sh\n"
        f'[ -f "{root}/raced" ] && exit 0\n'
        f'touch "{root}/raced"\n'
        f'{real} clone -q "{bare}" "{root}/racer" || exit 0\n'
        f'cd "{root}/racer" || exit 0\n'
        f"{real} config user.email o@z\n"
        f"{real} config user.name other\n"
        f'{real} checkout -q -B "{branch}" "origin/{branch}"\n'
        "echo raced > raced.txt\n"
        f"{real} add -A\n"
        f'{real} commit -qm "chore: raced"\n'
        f'{real} push -q origin "{branch}"\n'
    )
    racer.chmod(0o755)
    bindir.mkdir(exist_ok=True)
    shim = bindir / "git"
    shim.write_text(
        "#!/bin/sh\n"
        f'out="$({real} "$@")" || exit $?\n'
        f'case " $* " in *" ls-remote "*) "{racer}" >/dev/null 2>&1 || true;; esac\n'
        "printf '%s\\n' \"$out\"\n"
    )
    shim.chmod(0o755)


def test_main_moving_a_file_this_batch_never_touched_is_not_a_conflict():
    """上一批把 `one.txt` 交付了，main 之后又改了它；下一批只新增一个无关文件。

    这必须成功。用天然共同祖先当基线的话，上一批那次改动会被当成「我这边的改动」
    再算一遍，于是 main 在交付之后对同一个文件的修改被判成冲突 —— 更糟的是新批次
    的分支根本建不出来，这一批无法开工。基线必须是**已经交付出去的那份内容**。
    """
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
            (work / "one.txt").write_text("delivered by batch one\n")
            _git(work, "add", "-A")
            _git(work, "commit", "-qm", "feat: batch one")
            _turn(work, sync, platform, bare, log)
            delivered_tip = _git(work, "rev-parse", "HEAD").strip()
            _squash_into_main(bare, root, "topic/one")

            # main 在交付**之后**又改了这个文件。
            _STAGING[0] += 1
            staging = root / f"after-{_STAGING[0]}"
            _git(root, "clone", "-q", str(bare), str(staging))
            _git(staging, "config", "user.email", "p@z")
            _git(staging, "config", "user.name", "platform")
            (staging / "one.txt").write_text("main improved it afterwards\n")
            _git(staging, "commit", "-qam", "fix: improve one.txt")
            _git(staging, "push", "-q", "origin", "main")
            main_sha = _git(staging, "rev-parse", "HEAD").strip()

            # 下一批只新增一个完全无关的文件。
            platform.payload = {
                "branch": "topic/two",
                "base": "main",
                "base_sha": main_sha,
                "on_delivered": True,
                "on_head": delivered_tip,
            }
            (work / "two.txt").write_text("batch two\n")
            _git(work, "add", "-A")
            _git(work, "commit", "-qm", "feat: batch two")

            reported = _turn(work, sync, platform, bare, log)
        finally:
            platform.stop()

        assert '"status":"ok"' in reported, reported
        assert "refs/heads/topic/two" in _refs(bare)
        assert _pr_would_show(bare, "main", "topic/two") == ["two.txt"]
        # main 在交付之后的那次修改**没有被回滚**。
        assert (
            _git(bare, "show", "topic/two:one.txt") == "main improved it afterwards\n"
        )


def test_a_third_batch_and_repeated_syncs_within_one_batch():
    """不是只有「第二批的第一次同步」成立。

    同一批里同步好几次（分身一轮一轮地干），以及第三批 —— 每一次都只能带着这一批
    自己的改动。
    """
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
            (work / "one.txt").write_text("batch one\n")
            _git(work, "add", "-A")
            _git(work, "commit", "-qm", "feat: batch one")
            assert '"status":"ok"' in _turn(work, sync, platform, bare, log)
            tip_one = _git(work, "rev-parse", "HEAD").strip()
            main_after_one = _squash_into_main(bare, root, "topic/one")

            platform.payload = {
                "branch": "topic/two",
                "base": "main",
                "base_sha": main_after_one,
                "on_delivered": True,
                "on_head": tip_one,
            }
            for nth in ("a", "b", "c"):
                (work / f"two-{nth}.txt").write_text(f"batch two {nth}\n")
                _git(work, "add", "-A")
                _git(work, "commit", "-qm", f"feat: batch two {nth}")
                assert '"status":"ok"' in _turn(work, sync, platform, bare, log)
                # 每一次都只带这一批的东西，反复同步不会越滚越多。
                assert _pr_would_show(bare, "main", "topic/two") == sorted(
                    f"two-{x}.txt" for x in "abc"[: "abc".index(nth) + 1]
                )

            # 第二批交付，第三批开工。上一批交出去的是**远端那条分支**的样子。
            tip_two = _git(bare, "rev-parse", "topic/two").strip()
            main_after_two = _squash_into_main(bare, root, "topic/two")
            platform.payload = {
                "branch": "topic/three",
                "base": "main",
                "base_sha": main_after_two,
                "on_delivered": True,
                "on_head": tip_two,
            }
            (work / "three.txt").write_text("batch three\n")
            _git(work, "add", "-A")
            _git(work, "commit", "-qm", "feat: batch three")

            assert '"status":"ok"' in _turn(work, sync, platform, bare, log)
        finally:
            platform.stop()

        assert _pr_would_show(bare, "main", "topic/three") == ["three.txt"]


def test_a_commit_that_reached_the_new_branch_first_is_not_overwritten():
    """别人的提交**在我们读之前**就已经在远端了 —— 照样不许覆盖。

    这是 CAS 挡不住的那一半：读取时刻算出来的「预期值」根本不是 lease，它只是
    「现在那儿是什么」，于是先到的贡献被读进来当成预期值，然后被理直气壮地盖掉。
    基准必须是**这个同步器自己上次发布的那个 SHA**。
    """
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
            (work / "one.txt").write_text("batch one\n")
            _git(work, "add", "-A")
            _git(work, "commit", "-qm", "feat: batch one")
            _turn(work, sync, platform, bare, log)
            tip_one = _git(work, "rev-parse", "HEAD").strip()
            main_sha = _squash_into_main(bare, root, "topic/one")

            # 第二批：先衔接一次，把 topic/two 建出来。
            platform.payload = {
                "branch": "topic/two",
                "base": "main",
                "base_sha": main_sha,
                "on_delivered": True,
                "on_head": tip_one,
            }
            (work / "two.txt").write_text("batch two\n")
            _git(work, "add", "-A")
            _git(work, "commit", "-qm", "feat: batch two")
            assert '"status":"ok"' in _turn(work, sync, platform, bare, log)

            # 另一个 clone 往同一批推了东西 —— **先于**下一轮的任何读取。
            _remote_commit(bare, root, "topic/two", "other.txt")
            (work / "local.txt").write_text("local\n")
            _git(work, "add", "-A")
            _git(work, "commit", "-qm", "feat: local")

            reported = _turn(work, sync, platform, bare, log)
        finally:
            platform.stop()

        assert "other.txt" in _tree_of(bare, "topic/two"), (
            "先到远端的那个提交被这次衔接抹掉了"
        )
        assert '"status":"failed"' in reported, reported


def test_a_batch_with_no_recorded_delivery_is_refused_rather_than_guessed():
    """上一批合并的时候没人记下「交出去的是哪个 commit」（迁移不回填），而房间已经
    换批了 —— 这时候**不许**退回普通推送。

    退回普通推送的结果正是这段代码要消灭的那个：一个把上一批改动又展示一遍的 PR，
    外加一个 `status=ok`。「没有依据」和「不用衔接」是两件事。
    """
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
            (work / "one.txt").write_text("batch one\n")
            _git(work, "add", "-A")
            _git(work, "commit", "-qm", "feat: batch one")
            _turn(work, sync, platform, bare, log)
            main_sha = _squash_into_main(bare, root, "topic/one")

            # 旧批次：合了，但没有记录下交付的那个 commit。
            platform.payload = {
                "branch": "topic/two",
                "base": "main",
                "base_sha": main_sha,
                "on_delivered": False,
                "on_head": "",
            }
            (work / "two.txt").write_text("batch two\n")
            _git(work, "add", "-A")
            _git(work, "commit", "-qm", "feat: batch two")
            (work / "scratch.txt").write_text("还没提交的东西\n")

            reported = _turn(work, sync, platform, bare, log)
        finally:
            platform.stop()

        assert '"status":"failed"' in reported, reported
        assert "refs/heads/topic/two" not in _refs(bare), (
            "没有交付依据也把这一批推出去了 —— 那个 PR 会把上一批再展示一遍"
        )
        assert "refs/cheese/snapshots/topic/two" in _refs(bare)
        _assert_the_report_is_safe_to_follow(reported, "topic/two")


def test_a_batch_that_has_not_changed_still_pushes_normally():
    """别误伤：分支没变的时候（同一批的下一轮），旧批次有没有交付记录都无所谓 ——
    根本没有衔接需求，普通推送就是对的。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bare = _platform_repo(root)
        work = _device_clone(root, bare, "topic/one")
        sync = root / "cheese-sync"
        sync.write_text(_sync_body())
        log = root / "hook.log"
        platform = _Platform()
        platform.payload = {"branch": "topic/one", "on_delivered": False, "on_head": ""}
        try:
            for nth in ("a", "b"):
                (work / f"{nth}.txt").write_text(f"{nth}\n")
                _git(work, "add", "-A")
                _git(work, "commit", "-qm", f"feat: {nth}")
                assert '"status":"ok"' in _turn(work, sync, platform, bare, log)
        finally:
            platform.stop()

        assert _tree_of(bare, "topic/one") == ["README.md", "a.txt", "b.txt"]


def _detail(reported: str) -> str:
    """The `detail` the hook carried — read as JSON, because that is how it is
    read on the other end. A detail with a quote in it does not arrive at all."""
    line = [x for x in reported.splitlines() if x.strip()][-1]
    return json.loads(line)["detail"]


def _assert_the_report_is_safe_to_follow(reported: str, branch: str) -> None:
    """接不上去的报告要是**能照着做**的：说得出没交付的提交怎么接过去、未提交的改
    动在哪个 ref 上、以及「重新 clone」排在这两件之后。

    「重新 clone」当第一句话是危险的：这一轮失败的意思正是这台机器上有平台那边没有
    的东西。
    """
    detail = _detail(reported)
    assert f"refs/cheese/snapshots/{branch}" in detail, detail
    assert "git fetch origin" in detail, detail
    reclone = detail.lower().index("re-clone only")
    assert reclone > detail.index(f"refs/cheese/snapshots/{branch}"), detail
    assert detail.lower().count("re-clone") == 1, detail


def _publish_like_the_old_script(work: Path, branch: str) -> None:
    """升级之前那个同步器干的事：把 HEAD 推上去，什么都不记。

    没有 `refs/cheese/published/*`、没有 `refs/cheese/local-at/*`、没有
    `.git/cheese-sync/branch` —— 这些 ref 是新脚本才开始写的，而升级不会追认
    一个已经在跑的 clone 推过什么。
    """
    _git(work, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")


def test_a_clone_from_before_the_upgrade_does_not_redeliver_the_last_batch():
    """这台机器**在升级之前**就已经在往上一批发布了，所以它一条本地记录都没有。

    平台照样说得出这件事：`?on=` 问的那一批 `on_delivered=true`，而现在这一批是
    另一条分支。只看本地记录的话，这台机器看起来像「从没发布过」，于是走普通推
    送 —— 新分支从上一批的提交上长出来，PR 把上一批的改动再展示一遍，`status=ok`。
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bare = _platform_repo(root)
        work = _device_clone(root, bare, "topic/one")
        sync = root / "cheese-sync"
        sync.write_text(_sync_body())
        log = root / "hook.log"
        platform = _Platform()
        try:
            (work / "one.txt").write_text("batch one\n")
            _git(work, "add", "-A")
            _git(work, "commit", "-qm", "feat: batch one")
            _publish_like_the_old_script(work, "topic/one")
            delivered_tip = _git(work, "rev-parse", "HEAD").strip()
            main_sha = _squash_into_main(bare, root, "topic/one")
            assert "refs/cheese/published/topic/one" not in _git(
                work, "for-each-ref", "--format=%(refname)"
            ), "这个用例要的是一个**没有**新脚本记录的 clone"

            platform.payload = {
                "branch": "topic/two",
                "base": "main",
                "base_sha": main_sha,
                "on_delivered": True,
                "on_head": delivered_tip,
            }
            (work / "two.txt").write_text("batch two\n")
            _git(work, "add", "-A")
            _git(work, "commit", "-qm", "feat: batch two")
            (work / "scratch.txt").write_text("还没提交的东西\n")

            reported = _turn(work, sync, platform, bare, log)
        finally:
            platform.stop()

        assert '"status":"ok"' not in reported, reported
        assert '"status":"failed"' in reported, reported
        # 上一批的改动没有被这一批再交付一次。
        assert "refs/heads/topic/two" not in _refs(bare), (
            "上一批的提交被当成新一批推了出去 —— 那个 PR 会把它们再展示一遍"
        )
        # 内容全在，而且报告说得出怎么取回来。
        assert "refs/cheese/snapshots/topic/two" in _refs(bare)
        assert (work / "scratch.txt").read_text() == "还没提交的东西\n"
        assert "refs/cheese/snapshots/topic/two" in reported, reported
        assert "git fetch" in reported, reported


def _run_from(detail: str, pattern: str, cwd: Path) -> None:
    """把 detail 里写的那条命令**原样跑一遍**。

    这是这条指引唯一说得清的验收：它是不是真的能把东西取回来。断言 detail 里出现
    了某几个词，证明的只是有人写过那几个词。
    """
    import re

    found = re.search(pattern, detail)
    assert found, f"{pattern!r} 不在这条指引里: {detail}"
    done = subprocess.run(
        ["sh", "-c", found.group(0)], cwd=cwd, capture_output=True, text=True
    )
    assert done.returncode == 0, f"{found.group(0)}: {done.stdout}{done.stderr}"


def test_the_refusal_can_be_followed_to_get_every_commit_and_edit_back():
    """指引里的命令是真的能跑通的，而且跑通之后交付出去的只有这一批。

    「Re-clone the workspace」对一台**有未交付提交、有未提交改动**的机器是条会删东
    西的建议 —— 而这正是拒绝发生时机器的样子。所以这里照着 detail 一条条粘：先把
    没交付的提交接到新一批上推出去，再从快照 ref 把未提交的改动捞回一个新 clone。
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bare = _platform_repo(root)
        work = _device_clone(root, bare, "topic/one")
        sync = root / "cheese-sync"
        sync.write_text(_sync_body())
        log = root / "hook.log"
        platform = _Platform()
        try:
            (work / "one.txt").write_text("batch one\n")
            _git(work, "add", "-A")
            _git(work, "commit", "-qm", "feat: batch one")
            _publish_like_the_old_script(work, "topic/one")
            delivered_tip = _git(work, "rev-parse", "HEAD").strip()
            main_sha = _squash_into_main(bare, root, "topic/one")

            platform.payload = {
                "branch": "topic/two",
                "base": "main",
                "base_sha": main_sha,
                "on_delivered": True,
                "on_head": delivered_tip,
            }
            (work / "two.txt").write_text("batch two\n")
            _git(work, "add", "-A")
            _git(work, "commit", "-qm", "feat: batch two")
            (work / "scratch.txt").write_text("还没提交的东西\n")

            reported = _turn(work, sync, platform, bare, log)
        finally:
            platform.stop()

        assert '"status":"failed"' in reported, reported
        _assert_the_report_is_safe_to_follow(reported, "topic/two")
        detail = _detail(reported)

        # 第一步：把没交付的提交接到新一批的 base 上，然后推出去。
        _run_from(detail, r"git fetch origin \S+ && git rebase --onto \S+ \S+", work)
        _run_from(detail, r"git push origin HEAD:refs/heads/[^\s.]+", work)
        # 交付出去的只有这一批自己的改动 —— 上一批没有被再带一次。
        assert _pr_would_show(bare, "main", "topic/two") == ["two.txt"]
        # 未提交的改动没有被这条路径碰过。
        assert (work / "scratch.txt").read_text() == "还没提交的东西\n"

        # 第二步：**只有到这里**才谈重新 clone —— 而未提交的改动捞得回来。
        fresh = root / "re-cloned"
        _git(root, "clone", "-q", "-b", "topic/two", str(bare), str(fresh))
        assert not (fresh / "scratch.txt").exists()
        _run_from(
            detail,
            r"git fetch origin refs/cheese/snapshots/\S+ "
            r"&& git checkout FETCH_HEAD -- \.",
            fresh,
        )
        assert (fresh / "scratch.txt").read_text() == "还没提交的东西\n"
