"""A member's own Claude Code whose execution environment is on another
machine than the one its session runs on (#2991, #2320).

The session host is the owner's computer, which has no forwarded project view
and no namespace of its own. So the session works in a directory of its own
there, holding a copy of the project's context, and everything it runs — its
commands, its files — runs in the execution environment, at the executor's
path. Driven through the interfaces the product uses: `client.prepare` against
a platform that hands out the lease, a real `runtime.py` executor behind it,
and the shell prefix the build execs.
"""

import json
import os
import shlex
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.pinned_claude import claude_binary

SOURCE = (
    Path(__file__).resolve().parents[2]
    / "app/domain/agent/harness/claude_code/remote_execution"
)
RUNTIME = SOURCE / "runtime.py"
CLIENT = SOURCE / "client.py"


def _request(state, method, params):
    answer = subprocess.run(
        [sys.executable, str(RUNTIME), "request", "--state", str(state)],
        input=json.dumps({"method": method, "params": params}),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert answer.returncode == 0, answer.stderr
    return json.loads(answer.stdout)


@pytest.fixture
def executor(tmp_path):
    """The execution environment: a project and the runtime serving it, on
    "another machine" — a directory the session host never looks into."""
    workspace = (tmp_path / "elsewhere" / "project").resolve()
    (workspace / ".claude/skills/deploy").mkdir(parents=True)
    (workspace / "src").mkdir()
    (workspace / "CLAUDE.md").write_text("Indent with tabs.\n")
    (workspace / ".claude/skills/deploy/SKILL.md").write_text(
        "---\nname: deploy\ndescription: Ship it.\n---\nRun make deploy.\n"
    )
    home = tmp_path / "elsewhere" / "home"
    home.mkdir()
    state = tmp_path / "elsewhere" / "state"
    started = subprocess.run(
        [sys.executable, str(RUNTIME), "start", "--state", str(state)],
        input=json.dumps(
            {
                "workspace": str(workspace),
                "claude": claude_binary(),
                "env": {"EXECUTOR_MARKER": "in-the-environment"},
            }
        ),
        capture_output=True,
        text=True,
        timeout=30,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(home),
            "LANG": "C.UTF-8",
            "SHELL": "/bin/bash",
        },
    )
    assert started.returncode == 0, started.stderr
    yield workspace, state
    subprocess.run(
        [sys.executable, str(RUNTIME), "stop", "--state", str(state)],
        capture_output=True,
        timeout=30,
    )


@pytest.fixture
def platform(executor):
    """The platform: hands out the session's lease on the executor, and
    carries its calls there."""
    workspace, state = executor

    class Platform(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path.endswith("/work-lease"):
                result = {
                    "data": {
                        "target": {
                            "kind": "device",
                            "workspace": str(workspace),
                            "url": base + "/execute",
                            "generation": "leased",
                        },
                        "token": "execution-token",
                    }
                }
            else:
                result = _request(state, body["method"], body["params"])
            encoded = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Platform)
    base = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield base
    server.shutdown()
    server.server_close()


@pytest.fixture
def session(tmp_path, executor, platform, monkeypatch):
    """The own session, prepared on the owner's computer the way the platform
    starts it when its environment is on another machine (`owner_provider`)."""
    from app.domain.agent.harness.claude_code.remote_execution import client

    workspace, _ = executor
    monkeypatch.setenv("CHEESE_API", platform)
    monkeypatch.setenv("CHEESE_TOKEN", "session-token")
    directory = tmp_path / "owners-computer" / "session"
    home = tmp_path / "owners-computer" / "home"
    launch = client.prepare(
        directory,
        {
            "kind": "deferred",
            "workspace": str(workspace),
            "lease_path": "/topics/t/sessions/s/work-lease",
            "setup_env": {},
            "mcp_servers": [],
            "local": True,
            "apart": True,
        },
        claude=claude_binary(),
        home_override=home,
        config_override=home / ".claude",
    )
    return launch, directory, home / ".claude"


def _run(launch, directory, command, cwd):
    """One Bash command, handed to the shell prefix the way the build does."""
    temporary = Path(launch["env"]["CLAUDE_CODE_TMPDIR"])
    cwd_file = temporary / "claude-ab12-cwd"
    cwd_file.unlink(missing_ok=True)
    wrapped = f"eval {shlex.quote(command)} < /dev/null && pwd -P >| {cwd_file}"
    done = subprocess.run(
        [launch["env"]["CLAUDE_CODE_SHELL_PREFIX"], wrapped],
        cwd=cwd,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(directory),
            "CHEESE_API": os.environ["CHEESE_API"],
            "CHEESE_TOKEN": os.environ["CHEESE_TOKEN"],
            "CLAUDE_CODE_TMPDIR": str(temporary),
        },
        capture_output=True,
        timeout=60,
    )
    return done, cwd_file.read_text() if cwd_file.exists() else None


def test_its_commands_and_files_are_the_environments_not_the_owners_computer(
    session, executor
):
    launch, directory, _config = session
    workspace, _ = executor
    seen = Path(launch["cwd"])
    # The session runs on the owner's computer, in no namespace, in a directory
    # of its own there: the executor's path is not one this computer has.
    assert "enter" not in launch["command"]
    assert seen.is_dir() and seen != workspace

    done, ended_in = _run(
        launch,
        directory,
        'cd src && printf "%s|%s\\n" "$PWD" "$EXECUTOR_MARKER" && touch made-here',
        seen,
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout == f"{workspace}/src|in-the-environment\n".encode()
    assert (workspace / "src/made-here").is_file()
    assert not (seen / "src/made-here").exists()
    # The build is told the directory the shell ended in where it sees the
    # project, and that directory is there for it to keep.
    assert ended_in == f"{seen}/src\n"
    assert (seen / "src").is_dir()

    done, _ = _run(launch, directory, "pwd", seen / "src")
    assert done.stdout == f"{workspace}/src\n".encode()


def test_it_reads_the_projects_context_where_it_starts_and_keeps_it_current(
    session, executor
):
    from app.domain.agent.harness.claude_code.remote_execution import client

    launch, directory, config = session
    workspace, _ = executor
    seen = Path(launch["cwd"])
    assert (seen / "CLAUDE.md").read_text() == "Indent with tabs.\n"
    assert "Run make deploy." in (config / "skills/deploy/SKILL.md").read_text()

    (workspace / "CLAUDE.md").write_text("Indent with spaces.\n")
    (workspace / ".claude/skills/deploy/SKILL.md").unlink()
    (workspace / ".claude/skills/deploy").rmdir()
    (workspace / ".claude/rules").mkdir()
    (workspace / ".claude/rules/naming.md").write_text("Name things plainly.\n")
    # What the runner does before each turn (`runner._catch_up`).
    client.sync_context(directory / "execution.json")

    assert (seen / "CLAUDE.md").read_text() == "Indent with spaces.\n"
    assert not (config / "skills/deploy").exists()
    assert (config / "rules/naming.md").read_text() == "Name things plainly.\n"
