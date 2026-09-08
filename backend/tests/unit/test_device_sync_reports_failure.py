"""A push that fails must leave a trace.

The audit ran the generated `cheese-sync` against a missing remote and observed
HOOK_EXIT=0 with the branch never created: the turn reported done while the only
copy of the agent's work sat on a machine nobody would think to look at.

Staying non-fatal was right — a Stop hook that dies takes the turn with it — but
silence was not. The script now reports its outcome through `cheese-hook`, which
already spools durably and retries until the backend acknowledges, so the same
failure still exits 0 and is no longer invisible.
"""

import os
import subprocess
import tempfile
from pathlib import Path

from app.domain.agent.harness.claude_code.device_launch import build_launch_script


def _sync_body() -> str:
    script = build_launch_script(sync_on_stop=True)
    return script.split("'SYNC'")[1].split("SYNC")[0]


def _branch_server(answer: str | None):
    """The platform answering「这批活现在写哪条分支」over a real socket.

    The script asks at push time rather than trusting the branch its screen
    started with, so a test of the script has to answer that question — and
    `answer=None` is the case that matters most: not knowing must not silently
    become "the branch this screen started on".
    """
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            if answer is None:
                self.send_response(404)
                self.end_headers()
                return
            body = json.dumps({"branch": answer}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _run(
    work: Path,
    remote: str,
    hook_log: Path,
    *,
    branch: str | None = "topic/abc",
) -> subprocess.CompletedProcess:
    """Run the real generated script with cheese-hook stubbed to a log."""
    bindir = work.parent / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "cheese-hook").write_text(f'#!/bin/sh\ncat >> "{hook_log}"\n')
    (bindir / "cheese-hook").chmod(0o755)
    sync = work.parent / "cheese-sync"
    sync.write_text(_sync_body())
    server = _branch_server(branch)
    host, port = server.server_address[:2]
    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "CHEESE_GIT_REMOTE": remote,
        # 冻在启动那一刻的那个值。脚本**不许**用它。
        "CHEESE_GIT_BRANCH": "topic/stale",
        "CHEESE_BRANCH_URL": f"http://{host}:{port}/branch",
        "CHEESE_TOPIC": "a-place",
        "CHEESE_WORK": str(work),
    }
    try:
        return subprocess.run(
            ["sh", str(sync)], env=env, capture_output=True, text=True, timeout=60
        )
    finally:
        server.shutdown()


def _repo_with_one_edit(work: Path, remote: str) -> None:
    """A workspace one commit deep with an uncommitted edit on top — the shape
    every turn ends in, and the one whose push has to be reported."""
    for args in (
        ["init", "-q"],
        ["config", "user.email", "c@z"],
        ["config", "user.name", "c"],
        ["remote", "add", "origin", remote],
    ):
        subprocess.run(["git", *args], cwd=work, capture_output=True)
    (work / "committed.txt").write_text("already the agent's own commit\n")
    subprocess.run(["git", "add", "-A"], cwd=work, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=work, capture_output=True)
    (work / "agent_wrote_this.txt").write_text("the user's only copy\n")


def test_a_rejected_push_is_reported_instead_of_swallowed():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        work = root / "work"
        work.mkdir()
        hook_log = root / "hook.log"
        _repo_with_one_edit(work, str(root / "nonexistent-remote"))

        result = _run(work, str(root / "nonexistent-remote"), hook_log)

        assert result.returncode == 0, "a Stop hook must never take the turn down"
        assert hook_log.exists(), "the failed push left no trace at all"
        reported = hook_log.read_text()
        assert '"status":"failed"' in reported, reported
        assert '"branch":"topic/abc"' in reported, reported


def test_a_successful_push_reports_the_commit_it_landed():
    """Success must be distinguishable from failure, and name what it pushed —
    an acknowledgement the backend can check the server ref against."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        remote = root / "remote.git"
        subprocess.run(
            ["git", "init", "-q", "--bare", str(remote)], capture_output=True
        )
        work = root / "work"
        work.mkdir()
        hook_log = root / "hook.log"
        _repo_with_one_edit(work, str(remote))

        result = _run(work, str(remote), hook_log)

        assert result.returncode == 0
        reported = hook_log.read_text()
        assert '"status":"ok"' in reported, reported
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=work, capture_output=True, text=True
        ).stdout.strip()
        assert head and head in reported, "the report must name the pushed commit"


def test_a_failed_sync_becomes_something_the_human_sees():
    """The report is only worth sending if it reaches a person.

    A turn whose work never left the machine looks exactly like one that
    succeeded — same assistant reply, same completed turn. That resemblance is
    the bug; it let a rejected push pass for delivered work until the machine
    was deleted and the work went with it.
    """
    from app.domain.agent.harness.claude_code.hook_events import translate_hook
    from app.domain.agent.service import AgentMessage

    failed = translate_hook(
        {"hook_event_name": "CheeseSync", "status": "failed", "branch": "topic/abc"}
    )

    assert isinstance(failed, AgentMessage), "a failed sync produced nothing visible"
    assert "topic/abc" in failed.text
    assert "机器" in failed.text, "it must say where the work actually is"


def test_a_successful_sync_stays_quiet():
    """Success needs no message — the work is in the branch already, and a
    notice every turn is noise that teaches people to ignore the warning."""
    from app.domain.agent.harness.claude_code.hook_events import translate_hook

    assert (
        translate_hook(
            {"hook_event_name": "CheeseSync", "status": "ok", "commit": "deadbeef"}
        )
        is None
    )


def test_a_branch_it_could_not_learn_is_reported_rather_than_guessed():
    """问不到「现在写哪条分支」时，不许退回启动那一刻冻住的那个值。

    那个值正是这条改动要消灭的东西：房间交付之后它指的是一条已经被 squash 进
    main 的分支，往它上面推，push 成功、钩子报 ok、代码谁也够不着。「不知道」和
    「还是老那条」是两件事，把后者当前者用，就是给一次错投盖上绿章。
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        remote = root / "remote.git"
        subprocess.run(
            ["git", "init", "-q", "--bare", str(remote)], capture_output=True
        )
        work = root / "work"
        work.mkdir()
        hook_log = root / "hook.log"
        _repo_with_one_edit(work, str(remote))

        result = _run(work, str(remote), hook_log, branch=None)

        assert result.returncode == 0, "a Stop hook must never take the turn down"
        reported = hook_log.read_text()
        assert '"status":"failed"' in reported, reported
        assert "topic/stale" not in reported, reported
        # 那条冻住的分支一个字节都没收到。
        refs = subprocess.run(
            ["git", "-C", str(remote), "for-each-ref", "--format=%(refname)"],
            capture_output=True,
            text=True,
        ).stdout
        assert "refs/heads/topic/stale" not in refs, refs
        # 但工作没有丢：未提交的东西照样进了快照 ref。
        assert "refs/cheese/snapshots/" in refs, refs
