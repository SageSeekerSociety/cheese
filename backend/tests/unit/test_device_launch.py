"""Device screen launcher: hooks settings + self-contained launch command."""

import contextlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid

import pytest

from app.domain.agent import machine_launcher
from app.domain.agent.harness.claude_code import device_launch, warm_session
from app.domain.agent.harness.claude_code.session_launch import ClaudeLaunch
from app.domain.agent.harness.launch import MachinePlace


def test_launch_timings_append_without_logging_credentials(tmp_path):
    script = device_launch.build_launch_script()
    start = script.index("cheese_launch_phase() {")
    end = script.index("cheese_launch_phase started", start)
    function = script[start:end]
    directory = tmp_path / ".cheese/launch"
    directory.mkdir(parents=True)
    env = {
        "REAL_HOME": str(tmp_path),
        "CHEESE_TOPIC": "test-room",
        "CHEESE_TOKEN": "must-not-be-logged",
        "EPOCHREALTIME": "123.456",
    }
    for _ in range(2):
        result = subprocess.run(
            ["sh", "-c", function + "cheese_launch_phase started"],
            env=env,
            capture_output=True,
            check=True,
        )
        assert result.stdout == result.stderr == b""
    assert (directory / "test-room.timing").read_text() == (
        "123.456 started\n123.456 started\n"
    )
    # A shell without the clock leaves diagnostics off and launch successful.
    env.pop("EPOCHREALTIME")
    subprocess.run(
        ["sh", "-c", function + "cheese_launch_phase skipped"], env=env, check=True
    )
    assert "skipped" not in (directory / "test-room.timing").read_text()


@pytest.mark.parametrize("adoption_exit", [0, 9])
@pytest.mark.parametrize("stage_exit", [0, 13])
def test_warm_adoption_waits_for_room_configuration(
    tmp_path, monkeypatch, adoption_exit, stage_exit
):
    owner = tmp_path / "owner"
    home = tmp_path / "room"
    work = tmp_path / "work"
    warm = owner / ".cheese/native-warm"
    warm.mkdir(parents=True)
    (warm / "state.json").write_text("{}")
    (warm / "ready").touch()
    (warm.parent / "warm-native-runner.py").write_text(
        "import os, sys\nfrom pathlib import Path\n"
        "def _native_alive(state):\n    return True\n"
        "def stage(*args, **kwargs):\n"
        f"    if {stage_exit}: raise SystemExit({stage_exit})\n"
        "def adopt_room(directory):\n"
        "    assert (Path(os.environ['HOME']) / '.cheese/cheese-drain.env').exists()\n"
        "    (Path(os.environ['HOME']) / 'adopted').touch()\n"
        f"    return {adoption_exit}\n"
        "def connection(directory, project, topic):\n"
        "    assert (Path(os.environ['HOME']) / 'adopted').exists()\n"
        "    return {'command': ['tmux', '-S', '/test/warm.sock', "
        "'attach-session', '-t', 'native-warm']}\n"
    )
    binary = owner / ".cheese/claude/versions" / device_launch.CLAUDE_PINNED_VERSION
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\necho '2.1.277 (fixture)'\n")
    binary.chmod(0o700)
    tmux = tmp_path / "tmux"
    tmux.write_text('#!/bin/sh\ntouch "$HOME/attached"\n')
    tmux.chmod(0o700)
    script = tmp_path / "launch.sh"
    script.write_text(device_launch.build_launch_script())
    with (tmp_path / "launcher.log").open("w") as output:
        process = subprocess.Popen(
            ["sh", str(script)],
            env={
                "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
                "HOME": str(owner),
                "CHEESE_HOME": str(home),
                "CHEESE_WORK": str(work),
                "CHEESE_PROJECT": str(uuid.uuid4()),
                "CHEESE_TOPIC": str(uuid.uuid4()),
                "CHEESE_RV_SOCK": str(tmp_path / "rv.sock"),
                "CHEESE_RV_TOKEN_FILE": str(tmp_path / "rv.token"),
                "TMUX": "/test/owner.sock,1,0",
                "TMUX_PANE": "%0",
            },
            stdout=output,
            stderr=output,
        )
        try:
            if stage_exit:
                assert process.wait(timeout=5) == stage_exit
                assert not (work / "waiting").exists()
                assert not (home / "adopted").exists()
                return
            assert process.wait(timeout=5) == adoption_exit
            assert (home / "adopted").exists()
            assert (home / "attached").exists() == (adoption_exit == 0)
            assert not (work / ".git").exists()
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)


@pytest.mark.parametrize("ready", [False, True])
def test_unavailable_spare_prepares_room_without_a_shared_checkout(
    tmp_path, monkeypatch, ready
):
    owner = tmp_path / "owner"
    warm = owner / ".cheese/native-warm"
    warm.mkdir(parents=True)
    (warm / "state.json").write_text("{}")
    if ready:
        (warm / "ready").touch()
    (warm.parent / "warm-native-runner.py").write_text(
        "def _native_alive(state):\n    return False\n"
        "def stage(*args, **kwargs):\n    raise SystemExit(99)\n"
    )
    binary = owner / ".cheese/claude/versions" / device_launch.CLAUDE_PINNED_VERSION
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\necho '2.1.277 (fixture)'\n")
    binary.chmod(0o700)
    work = tmp_path / "work"
    script = tmp_path / "launch.sh"
    launch = device_launch.build_launch_script()
    launch = launch[: launch.index("cheese_launch_phase files_written")]
    script.write_text(launch + '\n[ -z "$WARM_ROOT" ]\n')
    result = subprocess.run(
        ["sh", str(script)],
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(owner),
            "CHEESE_HOME": str(tmp_path / "home"),
            "CHEESE_WORK": str(work),
            "CHEESE_PROJECT": str(uuid.uuid4()),
            "CHEESE_TOPIC": str(uuid.uuid4()),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert work.is_dir()
    assert not (work / ".git").exists()
    assert not (warm / "binding.json").exists()


def test_native_claim_replaces_all_provider_proxy_variants(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    work = tmp_path / "work"
    work.mkdir()
    token = tmp_path / "rv-token"
    token.write_text("fixture")
    (tmp_path / "ready").touch()
    (tmp_path / "environment.json").write_text(
        json.dumps(
            {
                name: "http://old-provider"
                for name in (
                    "HTTPS_PROXY",
                    "https_proxy",
                    "HTTP_PROXY",
                    "http_proxy",
                    "ALL_PROXY",
                    "all_proxy",
                )
            }
        )
    )
    monkeypatch.setattr(warm_session, "_native_alive", lambda state: True)
    frames = []
    with tempfile.TemporaryDirectory(prefix="cw-") as sockets:
        path = sockets + "/claim"
        (tmp_path / "state.json").write_text(
            json.dumps(
                {
                    "home": str(home),
                    "workspace": str(work.resolve()),
                    "rendezvous": sockets + "/rv",
                    "token_file": str(token),
                    "claim_socket": path,
                    "claim_auth": "fixture",
                }
            )
        )
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(path)
            server.listen(1)
            server.settimeout(3)

            def receive():
                connection, _ = server.accept()
                with connection, connection.makefile("rb") as reader:
                    frames.append(json.loads(reader.readline()))

            receiver = threading.Thread(target=receive)
            receiver.start()
            warm_session.bind(
                tmp_path,
                project_id=str(uuid.uuid4()),
                topic_id=str(uuid.uuid4()),
                work=work,
                system_prompt="fixture",
                settings={
                    "env": {"HTTPS_PROXY": "http://room-meter", "NO_PROXY": "localhost"}
                },
            )
            receiver.join(timeout=3)
            assert not receiver.is_alive()
    persisted = json.loads((home / ".claude/settings.json").read_text())["env"]
    for environment in (frames[0]["env"], persisted):
        assert (
            environment["HTTPS_PROXY"]
            == environment["https_proxy"]
            == "http://room-meter"
        )
        assert environment["HTTP_PROXY"] == environment["http_proxy"] == ""
        assert environment["ALL_PROXY"] == environment["all_proxy"] == ""
        assert environment["NO_PROXY"] == environment["no_proxy"] == "localhost"


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is required")
def test_prepared_topic_is_dead_when_only_its_drainer_survives(tmp_path):
    directory = tmp_path / ".cheese/native-warm"
    directory.mkdir(parents=True)
    runner = directory.parent / "warm-native-runner.py"
    shutil.copyfile(warm_session.__file__, runner)
    topic = str(uuid.uuid4())
    (directory / "binding.json").write_text(json.dumps({"topic_id": topic}))
    with tempfile.TemporaryDirectory(prefix="cw-") as socket_dir:
        socket_path = socket_dir + "/s"

        def tmux(*args):
            return subprocess.check_output(
                ["tmux", "-S", socket_path, *args], text=True
            ).strip()

        try:
            pane = tmux(
                "-f",
                "/dev/null",
                "new-session",
                "-d",
                "-P",
                "-F",
                "#{pane_id}",
                "-s",
                "native-warm",
                "sleep 30",
            )
            (directory / "state.json").write_text(
                json.dumps({"socket": socket_path, "pane": pane})
            )
            tmux(
                "new-window",
                "-d",
                "-t",
                "native-warm",
                "-n",
                "cheese-drain",
                "sleep 30",
            )

            def probe():
                return subprocess.check_output(
                    ["sh", "-c", device_launch.DEVICE_ALIVE_PROBE],
                    text=True,
                    env={
                        **os.environ,
                        "HOME": str(tmp_path),
                        "CHEESE_ALIVE_TOPIC": topic,
                    },
                ).strip()

            assert probe() == "alive"
            tmux("kill-pane", "-t", pane)
            assert tmux("list-panes", "-a", "-F", "#{pane_dead}") == "0"
            assert probe() == "dead"
        finally:
            subprocess.run(
                ["tmux", "-S", socket_path, "kill-server"], capture_output=True
            )


def test_liveness_probe_distinguishes_a_running_topic_from_an_exited_one(tmp_path):
    executable = tmp_path / "claude"
    # A symlink keeps Python's libraries reachable under the process name.
    # Renamed Nix sleep and copied macOS system binaries can exit immediately.
    executable.symlink_to(sys.executable)
    topic = str(uuid.uuid4())
    process = subprocess.Popen(
        [str(executable), "-c", "import time; time.sleep(30)"],
        env={**os.environ, "CHEESE_TOPIC": topic},
    )

    def probe():
        return subprocess.check_output(
            ["sh", "-c", device_launch.DEVICE_ALIVE_PROBE],
            env={**os.environ, "CHEESE_ALIVE_TOPIC": topic},
            text=True,
        ).strip()

    try:
        assert probe() == "alive"
    finally:
        process.terminate()
        process.wait(timeout=5)
    assert probe() == "dead"


@pytest.mark.skipif(not os.path.exists("/proc/self/environ"), reason="Linux procfs")
@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("CHEESE_TOPIC", "{topic}-other"),
        ("CHEESE_TOPIC", "other-{topic}"),
        ("OTHER", "CHEESE_TOPIC={topic}"),
    ],
)
def test_liveness_probe_requires_an_exact_topic_environment_field(tmp_path, key, value):
    executable = tmp_path / "claude"
    executable.symlink_to(sys.executable)
    topic = str(uuid.uuid4())
    environment = {**os.environ, key: value.format(topic=topic)}
    if key != "CHEESE_TOPIC":
        environment.pop("CHEESE_TOPIC", None)
    process = subprocess.Popen(
        [str(executable), "-c", "import time; time.sleep(30)"], env=environment
    )
    try:
        result = subprocess.check_output(
            ["sh", "-c", device_launch.DEVICE_ALIVE_PROBE],
            env={**os.environ, "HOME": str(tmp_path), "CHEESE_ALIVE_TOPIC": topic},
            text=True,
        )
        assert result.strip() == "dead"
    finally:
        process.terminate()
        process.wait(timeout=5)


def _alive_probe(topic):
    return subprocess.check_output(
        ["sh", "-c", device_launch.DEVICE_ALIVE_PROBE],
        env={**os.environ, "CHEESE_ALIVE_TOPIC": topic},
        text=True,
    ).strip()


@pytest.mark.skipif(not os.path.exists("/proc/self/environ"), reason="Linux procfs")
@pytest.mark.parametrize(
    "relative", [".cheese/claude/versions/9.9.9", ".local/bin/claude"]
)
def test_liveness_probe_matches_a_session_by_its_own_executable(tmp_path, relative):
    """The two install layouts a screen is launched from: the pin at
    `~/.cheese/claude/versions/<v>`, and `~/.local/bin/claude`."""
    executable = tmp_path / relative
    executable.parent.mkdir(parents=True)
    executable.symlink_to(sys.executable)
    topic = str(uuid.uuid4())
    process = subprocess.Popen(
        [str(executable), "-c", "import time; time.sleep(30)"],
        env={**os.environ, "CHEESE_TOPIC": topic},
    )
    try:
        assert _alive_probe(topic) == "alive"
    finally:
        process.terminate()
        process.wait(timeout=5)
    assert _alive_probe(topic) == "dead"


@pytest.mark.skipif(not os.path.exists("/proc/self/environ"), reason="Linux procfs")
def test_liveness_probe_ignores_a_helper_that_merely_carries_a_claude_path(tmp_path):
    """The remote-execution helpers run as `<python> .../.claude/remote-execution/
    forwarded_fs.py` and inherit the screen's CHEESE_TOPIC. Matching the `claude`
    substring anywhere in a command line adopted such a helper as the session
    itself: the room was reported alive with its claude long gone, so every
    message sent to it was delivered to nobody and timed out, instead of the
    platform retiring the dead screen and opening a new one."""
    script = tmp_path / ".claude/remote-execution/forwarded_fs.py"
    script.parent.mkdir(parents=True)
    script.write_text("import time\ntime.sleep(30)\n")
    topic = str(uuid.uuid4())
    process = subprocess.Popen(
        [sys.executable, str(script)], env={**os.environ, "CHEESE_TOPIC": topic}
    )
    try:
        # The helper really is running; only a session is missing.
        assert process.poll() is None
        assert _alive_probe(topic) == "dead"
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_hooks_settings_wire_command_hook_to_forwarder():
    s = device_launch.hooks_settings()
    assert s["skipDangerousModePermissionPrompt"] is True
    # Every perception hook forwards via the `cheese-hook` COMMAND hook.
    events = (
        "SessionStart",
        "PreToolUse",
        "PostToolUse",
        "MessageDisplay",
        "SubagentStart",
        "SubagentStop",
        "Stop",
    )
    for event in events:
        entry = s["hooks"][event][0]
        assert entry["hooks"][0] == {"type": "command", "command": "cheese-hook"}
    # The remote user cannot reach the terminal's native option picker.
    assert set(s["permissions"]["deny"]) == {"AskUserQuestion"}


def _screen_launch(
    *,
    hook_url="http://h/sandbox/hooks/T",
    hook_token="tok",
    home_dir="/dev/home",
    work_dir="/dev/work",
    model=None,
    resume_session_id=None,
    extra_env=None,
    topic_id="",
    git_remote=None,
    execution_target=None,
    remote_control=False,
    ca_pem="",
) -> tuple[list[str], dict[str, str]]:
    """What the device channel now does: say where, ask the plan what to run.

    A helper here rather than in the product, because assembling the two halves
    is the CHANNEL's job — this is how a test reaches the same result without
    standing up a device.
    """
    place = MachinePlace(
        home=home_dir,
        workdir=work_dir,
        store="$HOME/.cheese/store/P",
        state="$HOME/.cheese/harness/p/r/claude-code/deadbeef",
        api_base="http://h",
        project_id="P",
        topic_id=topic_id,
        agent_handle="ops",
        git_remote=git_remote,
        execution_target=execution_target,
        remote_control=remote_control,
        ca_pem=ca_pem,
    )
    plan = ClaudeLaunch(
        system_prompt="", model=model, resume_session_id=resume_session_id
    )
    command, env = machine_launcher.screen_launch(
        place, plan.on(place), hook_url=hook_url, token=hook_token
    )
    env.update(extra_env or {})
    return command, env


def test_build_screen_launch_shapes_command_and_env():
    command, env = _screen_launch(
        hook_url="http://h/sandbox/hooks/T",
        hook_token="scoped-tok",
        home_dir="/dev/home",
        work_dir="/dev/work",
        model="glm-5.2",
        extra_env={"ANTHROPIC_BASE_URL": "http://gw"},
    )
    assert command[0] == "bash" and command[1] == "-lc"
    script = command[2]
    # Self-contained launcher: writes settings + forwarder, spools+drains hooks, then
    # hosts claude in a persistent tmux session (direct exec if tmux is absent).
    assert 'cat > "$HOME/.claude/settings.json"' in script
    assert '"enableArtifact": false' in script
    assert "cheese-hook" in script
    # The binary is resolved (pin → ~/.local/bin → PATH) rather than taken from
    # PATH blindly, so the flags ride on $CLAUDE_BIN.
    assert '\\"$CLAUDE_BIN\\" --dangerously-skip-permissions' in script
    # A remote screen is just as unreachable for AskUserQuestion's in-terminal
    # picker as the local pane is — same deny, carried on the launch line.
    assert "--disallowedTools AskUserQuestion" in script
    assert "CHEESE_HOOK_SPOOL" in script
    # Env carries the hook wiring, home/work, model, and the gateway var.
    assert env["CHEESE_HOOK_URL"] == "http://h/sandbox/hooks/T"
    assert env["CHEESE_TOKEN"] == "scoped-tok"
    assert env["CHEESE_HOME"] == "/dev/home" and env["CHEESE_WORK"] == "/dev/work"
    assert env["CLAUDE_MODEL"] == "glm-5.2"
    assert env["ANTHROPIC_BASE_URL"] == "http://gw"
    assert "CHEESE_CLAUDE_GATES" not in env


def test_agent_authors_real_commit_and_platform_commits_it(tmp_path):
    import os
    import subprocess

    _, env = _screen_launch(
        hook_url="http://h/hooks",
        hook_token="test",
        home_dir=str(tmp_path),
        work_dir=str(tmp_path),
        model="test",
        git_remote="http://h/projects/P/git",
    )
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "Fix"],
        cwd=tmp_path,
        env={**os.environ, **env},
        check=True,
        capture_output=True,
    )
    actual = subprocess.check_output(
        ["git", "log", "-1", "--format=%an <%ae>%n%cn <%ce>"], cwd=tmp_path, text=True
    )
    assert actual.splitlines() == [
        "ops <ops@agent.cheese.local>",
        "芝士 <cheese@zhishi.local>",
    ]


def test_the_room_bounds_how_deep_and_how_wide_its_work_can_go():
    # A piece of work IS a subagent of the room's session, so the room's two
    # structural limits are these env vars and nothing else enforces them.
    _, env = _screen_launch(
        hook_url="http://h/sandbox/hooks/T",
        hook_token="tok",
        home_dir="/dev/home",
        work_dir="/dev/work",
    )
    # Work does not split further: a grandchild binds to no card and no person
    # can address it.
    assert env["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"] == "1"
    # How many pieces of work a room runs at once, sharing one worktree.
    assert env["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"] == "4"


def test_forwarder_posts_hook_json_with_token():
    script = device_launch.build_launch_script()
    assert "X-Cheese-Token: $CHEESE_TOKEN" in script
    assert "--data-binary @-" in script
    # Per-project trust is pre-accepted for the RESOLVED work dir (canonicalized).
    assert "pwd -P" in script
    assert '"hasTrustDialogAccepted":true' in script


def _commands_in(script: str) -> list[str]:
    """The lines the shell would run, with heredoc bodies left out.

    A skill travels inside the script — `cat > ... <<'CHEESE_NATIVE_SKILL'`, then
    the file, then the marker. Those lines are DATA. Reading the script as a flat
    list of lines would judge a Python variable named `node` to be a node
    command, which is exactly the mistake to avoid in a test about what the
    script runs.
    """
    commands: list[str] = []
    body_until: str | None = None
    for line in script.splitlines():
        if body_until is not None:
            if line.strip() == body_until:
                body_until = None
            continue
        opened = re.search(r"<<-?'?([A-Za-z_][A-Za-z0-9_]*)'?", line)
        if opened:
            body_until = opened.group(1)
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            commands.append(stripped)
    return commands


def test_the_launch_needs_no_interpreter_the_machine_may_not_have():
    """A machine whose `claude` is the native binary has no node.

    MicroCloud's Debian image is exactly that, and the script runs under `set -e`
    — so building the first-launch gates with node aborted the whole launch. The
    screen opened, claude never started, and the turn hung with nothing anywhere
    saying why.
    """
    script = device_launch.build_launch_script()
    # Only executable lines matter — the comment above the replacement explains
    # why node is gone and would match a naive substring check.
    assert not any(c.startswith("node ") for c in _commands_in(script))
    assert "mktrust" not in script
    # The gates still land — inside CLAUDE_CONFIG_DIR, where claude reads them
    # once the config dir is set — with the work dir the shell resolved.
    assert 'cat > "$CLAUDE_CONFIG_DIR/.claude.json"' in script
    assert '"bypassPermissionsModeAccepted":true' in script
    assert '"$CHEESE_WORK"' in script


def test_the_proxy_ca_rides_the_script_and_names_its_real_path():
    """Subscription turns: the backend's CA path means nothing on the device, and
    the server cannot know the device user's home — so the CA BYTES travel in the
    script, and the script itself exports NODE_EXTRA_CA_CERTS at the real
    (post-substitution) location."""
    pem = "-----BEGIN CERTIFICATE-----\nDEVCA\n-----END CERTIFICATE-----"
    script = device_launch.build_launch_script(ca_pem=pem)
    assert "cat > \"$HOME/.claude/proxy-ca.pem\" <<'CHEESECA'" in script
    assert "DEVCA" in script
    assert 'export NODE_EXTRA_CA_CERTS="$HOME/.claude/proxy-ca.pem"' in script


def test_no_ca_means_no_ca_block():
    """The gateway path must not write a stray cert file or export a CA path
    that points at nothing (an empty NODE_EXTRA_CA_CERTS breaks TLS wholesale)."""
    script = device_launch.build_launch_script()
    assert "CHEESECA" not in script
    assert 'export NODE_EXTRA_CA_CERTS="$HOME/.claude/proxy-ca.pem"' not in script


def test_build_screen_launch_threads_the_ca_through():
    command, _env = _screen_launch(
        hook_url="http://h/sandbox/hooks/T",
        hook_token="t",
        home_dir="/h",
        work_dir="/w",
        ca_pem="-----BEGIN CERTIFICATE-----\nDEVCA\n-----END CERTIFICATE-----",
    )
    assert "DEVCA" in command[2]


# --- the spool drainer's lifecycle (it must live and die with claude) --------


def _drain_body() -> str:
    """The cheese-drain script exactly as the launcher writes it to the device."""
    script = device_launch.build_launch_script()
    return script.split("<<'DRAIN'\n", 1)[1].split("\nDRAIN\n", 1)[0] + "\n"


def test_drainer_config_is_rewritten_each_launch_and_read_each_pass():
    """An adopted drainer outlives the turn that started it, so it must deliver
    with the CURRENT turn's token/URL: the launcher rewrites the config file
    atomically on every run, and the loop re-sources it on every pass."""
    script = device_launch.build_launch_script()
    tmp = '"$HOME/.cheese/cheese-drain.env.tmp"'
    assert f"cat > {tmp}" in script, "config must be staged to a tmp file"
    assert f"mv {tmp}" in script, "and moved into place atomically"
    for line in (
        'CHEESE_HOOK_SPOOL="$CHEESE_HOOK_SPOOL"',
        'CHEESE_HOOK_URL="$CHEESE_HOOK_URL"',
        'CHEESE_TOKEN="$CHEESE_TOKEN"',
    ):
        assert line in script


def _write_drainer(tmp_path, *, curl_response: str) -> tuple:
    """Materialize the generated drain script + its config + a stub curl."""
    drain = tmp_path / "cheese-drain"
    drain.write_text(_drain_body())
    spool = tmp_path / "spool"
    spool.mkdir()
    (tmp_path / "cheese-drain.env").write_text(
        f'CHEESE_HOOK_SPOOL="{spool}"\n'
        'CHEESE_HOOK_URL="http://backend.test/hooks"\n'
        'CHEESE_TOKEN="tok"\n'
    )
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "curl").write_text(f"#!/bin/sh\necho '{curl_response}'\n")
    (bindir / "curl").chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}
    return drain, spool, env


def test_drainer_delivers_the_spool_and_deletes_only_on_code_200(tmp_path):
    """Run the REAL generated script: a spooled event is posted and removed on
    a durable ack, and the pid file (the relaunch idempotence handle) appears."""
    drain, spool, env = _write_drainer(tmp_path, curl_response='{"code":200}')
    event = spool / "1700000000.ev1"
    event.write_text('{"hook_event_name":"Stop"}')
    proc = subprocess.Popen(["sh", str(drain)], env=env)
    try:
        deadline = time.monotonic() + 10
        while event.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not event.exists(), "the drainer never delivered the spooled event"
        assert (tmp_path / "cheese-drain.pid").exists()
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.parametrize("response", ['{"code":500}', '{"code":2000}', "invalid"])
def test_drainer_keeps_an_unacknowledged_event(tmp_path, response):
    drain, spool, env = _write_drainer(tmp_path, curl_response=response)
    event = spool / "1700000000.ev1"
    event.write_text('{"hook_event_name":"Stop"}')
    proc = subprocess.Popen(["sh", str(drain)], env=env)
    try:
        time.sleep(1.0)  # a couple of passes
        assert event.exists(), "an unacknowledged event must stay spooled"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_running_drainer_uses_rotated_delivery_configuration(tmp_path):
    drain, spool, env = _write_drainer(tmp_path, curl_response='{"code":200}')
    calls = tmp_path / "calls"
    (tmp_path / "bin/curl").write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$@" >> "{calls}"\necho \'{{"code":200}}\'\n'
    )
    proc = subprocess.Popen(["sh", str(drain)], env=env)
    try:
        for token in ("first-token", "rotated-token"):
            staged = tmp_path / "config.new"
            staged.write_text(
                f'CHEESE_HOOK_SPOOL="{spool}"\n'
                f'CHEESE_HOOK_URL="http://backend.test/{token}"\n'
                f'CHEESE_TOKEN="{token}"\n'
            )
            staged.replace(tmp_path / "cheese-drain.env")
            event = spool / f"0000000000000000001.{token}"
            event.write_text("{}")
            deadline = time.monotonic() + 5
            while event.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert not event.exists()
            assert f"X-Cheese-Token: {token}" in calls.read_text()
            assert f"http://backend.test/{token}" in calls.read_text()
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_drainer_prunes_only_claims_and_keeps_unacknowledged_events(tmp_path):
    drain, spool, env = _write_drainer(tmp_path, curl_response='{"code":500}')
    expired = [spool / ".n0000000000000000001"]
    unacknowledged = spool / "0000000000000000001.old"
    sequence = spool / ".seq"
    for path in [*expired, sequence, unacknowledged]:
        path.write_text("1")
        os.utime(path, (time.time() - 90000, time.time() - 90000))
    fresh = spool / "0000000000000000002.fresh"
    fresh.write_text("{}")
    proc = subprocess.Popen(["sh", str(drain)], env=env)
    try:
        deadline = time.monotonic() + 5
        while any(path.exists() for path in expired) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert all(not path.exists() for path in expired)
        assert unacknowledged.exists()
        assert fresh.exists()
        assert sequence.read_text() == "1"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_a_revived_drainer_exits_when_its_tether_dies(tmp_path):
    """The revival path runs the drainer in its own tmux window; the tether is
    what stops that window from keeping the session alive after claude exits."""
    drain, _spool, env = _write_drainer(tmp_path, curl_response='{"code":200}')
    corpse = subprocess.Popen(["sh", "-c", "exit 0"])
    corpse.wait()
    env["CHEESE_DRAIN_TETHER"] = str(corpse.pid)
    proc = subprocess.Popen(["sh", str(drain)], env=env)
    assert proc.wait(timeout=5) == 0


def test_the_gates_written_are_valid_json():
    """The file claude reads must parse — a heredoc makes that easy to get wrong."""
    import json as _json
    import re

    script = device_launch.build_launch_script()
    body = re.search(
        r'cat > "\$CLAUDE_CONFIG_DIR/\.claude\.json" <<JSON\n(.*?)\nJSON', script, re.S
    )
    assert body, "the gates heredoc is not where the launcher writes it"
    parsed = _json.loads(body.group(1).replace("$CHEESE_WORK", "/w"))
    assert parsed["bypassPermissionsModeAccepted"] is True
    assert parsed["projects"]["/w"]["hasTrustDialogAccepted"] is True


def _supervisor_block():
    script = device_launch.build_launch_script()
    marker = "# The connector owns the terminal session."
    return "set -e\n" + marker + script.split(marker, 1)[1]


def _spawn_supervisor(socket, env, body):
    import shlex
    import shutil

    tmux = shutil.which("tmux")
    if (
        subprocess.run(
            [tmux, "-S", socket, "has-session", "-t", "=screen"], capture_output=True
        ).returncode
        == 0
    ):
        return
    keys = (
        "PATH",
        "HOME",
        "CHEESE_WORK",
        "CLAUDE",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CHEESE_HOOK_SPOOL",
        "CHEESE_TOKEN_EXPIRES",
        "CHEESE_ENVIRONMENT",
    )
    command = shlex.join(
        [
            "env",
            *[f"{k}={env[k]}" for k in keys if k in env],
            "sh",
            "-c",
            'cd "$CHEESE_WORK"; ' + body,
        ]
    )
    subprocess.run(
        [tmux, "-S", socket, "new-session", "-d", "-s", "screen", command], check=True
    )


STUB_SOCK = "/tmp/cheese-test-connector.sock"


def _stub_tmux_env(tmp_path):
    """A home + a stub `tmux` that records kill/new/window to a log and toggles a
    has-session marker, so a run's session-lifecycle decisions are observable.

    The stub distinguishes the two servers a launch can talk to: one named with
    `-S` (ours) and the machine owner's DEFAULT one (no `-S`). Calls against the
    default server are logged with a `default:` prefix so a test can assert we
    never host anything there."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    work = home / "work"
    work.mkdir()
    log = tmp_path / "tmux.log"
    mark = tmp_path / "session.mark"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "tmux"
    stub.write_text(
        "#!/bin/sh\n"
        'sock=""\n'
        'if [ "$1" = "-S" ]; then sock="$2"; shift 2; fi\n'
        'cmd="$1"; shift\n'
        'if [ -z "$sock" ]; then\n'
        '  printf \'default:%s\\n\' "$cmd" >> "$STUB_LOG"\n'
        '  case "$cmd" in\n'
        '    has-session) [ -f "$STUB_OWNER_MARK" ] ;;\n'
        '    kill-session) rm -f "$STUB_OWNER_MARK" ;;\n'
        "    *) : ;;\n"
        "  esac\n"
        "  exit\n"
        "fi\n"
        'printf \'%s\\n\' "$sock" >> "$STUB_SOCKS"\n'
        'case "$cmd" in\n'
        '  has-session) [ -f "$STUB_MARK" ] ;;\n'
        '  kill-session) printf \'kill\\n\' >> "$STUB_LOG"; rm -f "$STUB_MARK" ;;\n'
        "  new-session) printf 'new\\n' >> \"$STUB_LOG\";"
        ' printf \'%s\\n\' "$@" >> "$STUB_ARGS"; : > "$STUB_MARK" ;;\n'
        "  new-window) printf 'window\\n' >> \"$STUB_LOG\" ;;\n"
        "  list-panes) printf '%s\\n' \"${STUB_PANES:-12345}\" ;;\n"
        "  attach) printf 'attach\\n' >> \"$STUB_LOG\" ;;\n"
        "  *) : ;;\n"
        "esac\n"
    )
    stub.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "HOME": str(home),
        "CHEESE_HOME": str(home),
        "CHEESE_WORK": str(work),
        "CLAUDE": "claude --model x",
        "TMUX": f"{STUB_SOCK},1,0",
        "STUB_LOG": str(log),
        "STUB_MARK": str(mark),
        "STUB_OWNER_MARK": str(tmp_path / "owner-session.mark"),
        "STUB_SOCKS": str(tmp_path / "sockets.seen"),
        "STUB_ARGS": str(tmp_path / "newsession.args"),
    }
    return home, env, log


def test_full_launcher_installs_platform_cli_without_network(tmp_path):
    home, env, _log = _stub_tmux_env(tmp_path)
    bindir = tmp_path / "bin"
    network = tmp_path / "network.calls"
    curl = bindir / "curl"
    curl.write_text(f'#!/bin/sh\necho attempted >> "{network}"\nexit 1\n')
    curl.chmod(0o755)
    claude = home / ".local/bin/claude"
    claude.parent.mkdir(parents=True)
    claude.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "2.1.277 (Claude Code)"; '
        'else printf "%s" "$BUN_OPTIONS" > "$HOME/bun-options"; fi\n'
    )
    claude.chmod(0o755)
    env["CHEESE_TOKEN_EXPIRES"] = str(int(time.time()) + 3600)
    # Devices execute a shipped file; Linux rejects this script's size in argv.
    launcher = tmp_path / "launch.sh"
    launcher.write_text(device_launch.build_launch_script())
    result = subprocess.run(
        ["sh", str(launcher)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert not network.exists(), "room startup must not fetch the platform CLI"
    transport = home / ".claude/webfetch_transport.cjs"
    assert (
        transport.read_bytes()
        == (
            device_launch.Path(device_launch.__file__).with_name(
                "webfetch_transport.cjs"
            )
        ).read_bytes()
    )
    assert (home / "bun-options").read_text() == f'"--preload={transport}"'
    installed = home / ".cheese/cheese"
    source = (
        device_launch.Path(device_launch.__file__).resolve().parents[5]
        / "sandbox/cheese"
    )
    assert installed.read_bytes() == source.read_bytes()
    result = subprocess.run(
        [str(installed), "--help"], env=env, capture_output=True, text=True, timeout=5
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


def test_hosted_launch_preserves_owner_and_project_while_installing_skills(tmp_path):
    owner, env, _log = _stub_tmux_env(tmp_path)
    work = tmp_path / "project"
    config = work / ".claude"
    config.mkdir(parents=True)
    protected = [
        owner / ".claude/settings.json",
        owner / ".claude/CLAUDE.md",
        owner / ".zshrc",
        owner / ".gitconfig",
        work / "CLAUDE.md",
        config / "settings.json",
        config / "skills/owner-skill/SKILL.md",
    ]
    for path in protected:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}" if path.suffix == ".json" else "owner content\n")
    before = {path: path.read_bytes() for path in protected}
    project_entries = set(work.rglob("*"))
    original_entries = set(owner.iterdir())
    session = owner / ".cheese/home/room"
    previous_chat_skill = session / ".claude/skills/cheese-chat/SKILL.md"
    previous_chat_skill.parent.mkdir(parents=True)
    previous_chat_skill.write_text("Previous generated chat guide\n")
    env.update(
        CHEESE_HOME=str(session),
        CHEESE_WORK=str(work),
        CHEESE_API="https://fixture.invalid",
        CHEESE_TOKEN_EXPIRES=str(int(time.time()) + 3600),
    )
    curl = tmp_path / "bin/curl"
    curl.write_text(
        '#!/bin/sh\nwhile [ "$#" -gt 0 ]; do\n'
        'if [ "$1" = "-o" ]; then shift; dest="$1"; fi\nshift\ndone\n'
        "cat > \"$dest\" <<'AGENT'\n#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "2.1.277 (Claude Code)"; '
        'else printf "%s\\n" "$@" "$CLAUDE_CONFIG_DIR" > "$HOME/agent.args"; '
        "fi\nAGENT\n"
    )
    curl.chmod(0o755)
    launcher = tmp_path / "launch.sh"
    launcher.write_text(device_launch.build_launch_script())
    result = subprocess.run(
        ["sh", str(launcher)], env=env, capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stderr
    assert {path: path.read_bytes() for path in protected} == before
    assert set(owner.iterdir()) - original_entries == {owner / ".cheese"}
    assert set(work.rglob("*")) == project_entries
    assert (
        owner / ".cheese/claude/versions" / device_launch.CLAUDE_PINNED_VERSION
    ).is_file()
    for name in ("cheese-docs",):
        assert (session / ".claude/skills" / name / "SKILL.md").is_file()
    assert not previous_chat_skill.exists()
    args = (session / "agent.args").read_text()
    assert "--dangerously-skip-permissions" in args
    assert f"{session}/.claude" in args


def _tmux_ge_30() -> bool:
    import shutil

    if not shutil.which("tmux"):
        return False
    out = subprocess.run(["tmux", "-V"], capture_output=True, text=True).stdout
    m = re.search(r"(\d+)\.(\d+)", out)
    return bool(m) and (int(m.group(1)), int(m.group(2))) >= (3, 0)


@pytest.mark.skipif(not _tmux_ge_30(), reason="needs a real tmux >= 3.0")
def test_environment_prepares_tools_without_task_code_on_attach_and_reset(tmp_path):
    import json
    import shutil
    import sys

    from app.domain.agent import environment_runner
    from app.domain.project.environment import EnvironmentConfig

    socket = f"/tmp/ce{os.getpid()}.sock"  # noqa: S108 — test-owned tmux socket
    home = tmp_path / "home"
    helpers = home / ".cheese"
    helpers.mkdir(parents=True)
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    shutil.copy(environment_runner.__file__, helpers / "cheese-environment.py")
    (helpers / "cheese-drain").write_text("#!/bin/sh\nexit 0\n")
    agent = tmp_path / "fake agent.sh"
    agent.write_text(
        "#!/bin/sh\ntest -f setup || exit 1\necho agent >> agents\nexec sleep 60\n"
    )
    agent.chmod(0o755)
    config = EnvironmentConfig(
        setup_script="echo setup >> setup",
        startup_script="cd backend\necho startup >> startup",
    )
    env = {
        **os.environ,
        "HOME": str(home),
        "CHEESE_WORK": str(work),
        "TMUX": f"{socket},1,0",
        "CLAUDE": f'"{agent}"',
        "CHEESE_ENVIRONMENT": json.dumps(config.snapshot()),
        "CHEESE_TOKEN_EXPIRES": str(int(time.time()) + 3600),
    }

    def attach():
        # The supervisor carries the environment wrapper itself, so the block
        # below is the whole of what a launch runs after the harness's own half.
        _spawn_supervisor(socket, env, _supervisor_block())

    def wait_agents(count):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if (work / "agents").exists() and len(
                (work / "agents").read_text().splitlines()
            ) == count:
                return
            time.sleep(0.05)
        pytest.fail("agent did not start after preparation")

    try:
        attach()
        wait_agents(1)
        attach()
        assert not (work / "startup").exists()
        reset = subprocess.run(
            [sys.executable, str(helpers / "cheese-environment.py"), "reset"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert reset.returncode == 0, reset.stderr
        assert (work / "agents").exists()
        attach()
        wait_agents(2)
        assert (work / "setup").read_text() == "setup\n"
        assert not (work / "startup").exists()
    finally:
        subprocess.run(["tmux", "-S", socket, "kill-server"], capture_output=True)


@pytest.mark.skipif(not _tmux_ge_30(), reason="needs a real tmux >= 3.0")
def test_fresh_token_overrides_a_stale_tmux_server_global(tmp_path):
    """End-to-end against a REAL tmux server whose GLOBAL env holds a stale token
    (the box's exact condition). A brand-new claude must boot carrying THIS
    launch's fresh token — proving the frozen-global inheritance (the 407 root
    cause) is overridden, not merely that a new process was spawned."""
    import shutil

    real_tmux = shutil.which("tmux")
    # A short socket path: a unix socket path is capped near 104 chars, and
    # pytest's tmp_path alone already blows past it on macOS.
    sock = f"/tmp/ct{os.getpid()}.sock"  # noqa: S108 — ephemeral, kill-server'd below
    home = tmp_path / "home"
    for name in (".claude", ".cheese"):
        (home / name).mkdir(parents=True, exist_ok=True)
    work = home / "work"
    work.mkdir()
    (home / ".cheese" / "cheese-drain").write_text("#!/bin/sh\nsleep 3\n")
    token_out = tmp_path / "claude_token.out"
    fake_claude = tmp_path / "fakeclaude.sh"
    # Write-then-rename: the waiter below keys on the file EXISTING, and a plain
    # `> file` creates it empty before printenv writes — on a loaded CI runner
    # the reader wins that race and sees ''. The rename makes it appear complete.
    # HOME and PATH ride along: the seed server's global env froze the test
    # runner's real values, so if -e does not carry them the inner claude leaks
    # the first-launcher's identity (the cross-topic hook mis-routing of
    # 2026-08-15 — topic B's claude running with topic A's HOME and PATH).
    # CHEESE_HOOK_SPOOL stands in for the UNLISTED vars: it is not on the -e
    # list, so only the sourced env dump can carry it — the seed server global
    # holds another topic's spool (the exact 2026-08-16 failure, where topic
    # E's hooks landed in topic F's spool and shipped under F's identity).
    fake_claude.write_text(
        f"#!/bin/sh\n{{ printenv CLAUDE_CODE_OAUTH_TOKEN; printenv HOME;\n"
        f'  printenv PATH; printenv CHEESE_HOOK_SPOOL; }} > "{token_out}.tmp"\n'
        f'mv "{token_out}.tmp" "{token_out}"\nsleep 3\n'
    )
    fake_claude.chmod(0o755)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    # PATH leads with a dir of our own so the assertion below can tell THIS
    # launch's PATH from the one frozen into the seed server's global env.
    (bindir / "keep").write_text("")
    try:
        subprocess.run(
            [real_tmux, "-S", sock, "new-session", "-d", "-s", "seed", "sleep 60"],
            env={
                **os.environ,
                "CLAUDE_CODE_OAUTH_TOKEN": "STALE-frozen-token",
                "CHEESE_HOOK_SPOOL": "/stale/other-topics/spool",
            },
            check=True,
        )
        env = {
            **os.environ,
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "HOME": str(home),
            "CHEESE_WORK": str(work),
            "CLAUDE": f"sh {fake_claude}",
            "CLAUDE_CODE_OAUTH_TOKEN": "FRESH-live-token",
            "CHEESE_HOOK_SPOOL": f"{home}/.cheese/cheese-spool",
            "CHEESE_TOKEN_EXPIRES": str(int(time.time()) + 100_000),
            # What a pane of the connector's own tmux sees. The launcher reads
            # the socket out of it, so the seeded server IS the one it hosts in
            # — no wrapper pinning it there.
            "TMUX": f"{sock},1,0",
        }
        _spawn_supervisor(sock, env, _supervisor_block())
        deadline = time.monotonic() + 8
        while not token_out.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert token_out.exists(), "the inner claude never launched"
        got = token_out.read_text().splitlines()
        assert got and got[0] == "FRESH-live-token", (
            "the new claude booted on the STALE server-global token, not this "
            f"launch's fresh one — the frozen-global 407 is not fixed: {got}"
        )
        assert got[1:2] == [str(home)], (
            f"the inner claude's HOME is not this launch's isolated home: {got}"
        )
        assert got[2:] and got[2].startswith(str(bindir)), (
            f"the inner claude's PATH does not lead with this launch's: {got}"
        )
        assert got[3:] == [f"{home}/.cheese/cheese-spool"], (
            "an UNLISTED var (CHEESE_HOOK_SPOOL) did not survive into the inner "
            f"claude — the sourced env dump is not reaching the session: {got}"
        )
    finally:
        subprocess.run([real_tmux, "-S", sock, "kill-server"], capture_output=True)
        try:
            os.unlink(sock)
        except OSError:
            pass


# --- the tunnel branch ------------------------------------------------------
# A remote machine cannot dial the meter's listener on the ghg network, so its
# CONNECT rides a helper on its own loopback. The helper is the fragile part:
# started in the wrong process tree it dies with the connector while claude
# lives on, pointed at a dead port — every turn then fails looking exactly like
# a stalled model, which is the most expensive failure to diagnose.


def _launch_with_tunnel(**overrides):
    env = {
        "CHEESE_TUNNEL_URL": "wss://gw.example/api/llm/tunnel",
        "CHEESE_TUNNEL_PORT": "8445",
        "CLAUDE_CODE_OAUTH_TOKEN": "scoped.session.token",
        "HTTPS_PROXY": "http://127.0.0.1:8445",
    }
    env.update(overrides)
    command, _env = _screen_launch(
        hook_url="http://h/sandbox/hooks/T",
        hook_token="scoped-tok",
        home_dir="/dev/home",
        work_dir="/dev/work",
        model="",
        extra_env=env,
    )
    return command[2]


def test_the_tunnel_launch_is_valid_shell():
    """The script is assembled from an f-string wrapping heredocs that now carry
    a whole python module; a mis-escaped brace or quote turns into a syntax
    error that would only surface on a real machine, mid-turn."""
    import subprocess

    checked = subprocess.run(
        ["bash", "-n"], input=_launch_with_tunnel(), text=True, capture_output=True
    )
    assert checked.returncode == 0, checked.stderr


def test_the_helper_and_its_token_are_written_every_launch():
    """The helper re-reads the token per connection, so rewriting this file is
    how a refreshed credential reaches a helper that is already running — the
    thing that stops #385 repeating one layer down."""
    script = _launch_with_tunnel()
    assert 'cat > "$HOME/.cheese/cheese-tunnel.py"' in script
    # The real module, not a paraphrase of it.
    assert "def open_tunnel(" in script and "Sec-WebSocket-Key" in script
    # Written atomically and mode-restricted: it holds a spendable token.
    assert 'chmod 600 "$HOME/.cheese/cheese-tunnel.token.tmp"' in script
    assert (
        'mv "$HOME/.cheese/cheese-tunnel.token.tmp" "$HOME/.cheese/cheese-tunnel.token"'
        in script
    )


def test_the_helper_starts_inside_the_session_and_before_claude():
    """Two properties, one line. INSIDE: backgrounded in the launcher's own tree
    it would die with the connector while claude survives in tmux. BEFORE:
    claude reads HTTPS_PROXY once and calls out immediately, so a helper that is
    still binding loses that race and the screen boots unauthenticated."""
    script = _launch_with_tunnel()
    # The wait itself, in the up-script — and NOT via bash's /dev/tcp: this runs
    # under `sh`, which is dash on the machine images, where that redirect fails
    # on every iteration and the loop would report "not ready" for a helper that
    # came up fine (verified on the real image: `cannot create /dev/tcp/...`).
    assert "/dev/tcp/" not in script
    assert 'python3 - "$CHEESE_TUNNEL_PORT"' in script


def test_a_deployment_without_a_tunnel_writes_and_runs_none_of_it():
    """Every deployment that has one today reaches the listener directly. The
    tunnel must cost them nothing — not a written file, not a no-op call."""
    script = _launch_with_tunnel(CHEESE_TUNNEL_URL="")
    # The guard is what makes it inert; the heredoc body may still be present.
    assert 'if [ -n "${CHEESE_TUNNEL_URL:-}" ]; then' in script


def test_a_helper_running_older_code_is_retired_not_adopted():
    """The launcher rewrites the helper on every launch, and `cheese-tunnel-up`
    adopts a live one. Without a version check a shipped fix would never reach a
    machine whose helper is still running — it would serve the old code forever,
    and nothing about that looks wrong from outside."""
    script = _launch_with_tunnel()
    # The stamp is what makes "same helper" decidable at all.
    assert "cheese-tunnel.stamp" in script
    assert 'cksum "$HOME/.cheese/cheese-tunnel.py"' in script
    # Adoption is conditional on it, and the mismatch path kills.
    assert '[ "$WANT" = "$HAVE" ]' in script
    assert 'kill "$PID"' in script


def _tunnel_up_home(tmp_path):
    """A HOME laid out the way the launcher leaves one, with a stub helper that
    binds its --port and then sits there — the only thing about the real helper
    this script cares about."""
    from app.domain.agent.machine_launcher import CHEESE_TUNNEL_UP

    home = tmp_path / "home"
    for name in (".claude", ".cheese"):
        (home / name).mkdir(parents=True, exist_ok=True)
    (home / ".cheese" / "cheese-tunnel.py").write_text(
        "import socket, sys, time\n"
        "port = int(sys.argv[sys.argv.index('--port') + 1])\n"
        "s = socket.socket()\n"
        "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        "s.bind(('127.0.0.1', port))\n"
        "s.listen(5)\n"
        "print('tunnel listening on 127.0.0.1:%d' % port, flush=True)\n"
        "time.sleep(300)\n"
    )
    (home / ".cheese" / "cheese-tunnel.token").write_text("")
    up = home / ".cheese" / "cheese-tunnel-up"
    up.write_text(CHEESE_TUNNEL_UP)
    up.chmod(0o755)
    return home, up


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _listening(port: int) -> bool:
    import socket

    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
    except OSError:
        return False
    return True


def _await_listening(port: int, *, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _listening(port):
            return True
        time.sleep(0.05)
    return False


def _kill_pidfile(home) -> None:
    import signal

    try:
        pid = int((home / ".cheese" / "cheese-tunnel.pid").read_text().strip())
    except (OSError, ValueError):
        return
    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGKILL)


@pytest.mark.skipif(not _tmux_ge_30(), reason="needs a real tmux >= 3.0")
def test_the_tunnel_helper_outlives_the_window_that_started_it(tmp_path):
    """The reuse path starts the helper as the command of its own tmux window,
    and that window is torn down the instant the command returns. A helper that
    dies with it leaves `claude` — which read HTTPS_PROXY once at startup and
    cannot be told a new one — dialling a dead port for the life of the screen.

    Measured 2026-08-30: fifteen topics in exactly that state, their
    `cheese-tunnel.log` showing `tunnel listening` at the last launch's
    timestamp, one of them re-@'d four times in three hours without a single
    reply."""
    import shutil

    home, _up = _tunnel_up_home(tmp_path)
    port = _free_port()
    tmux = shutil.which("tmux") or "tmux"  # the skipif above already found it
    sock = f"/tmp/cu{os.getpid()}.sock"  # noqa: S108 — ephemeral, killed below
    try:
        subprocess.run(
            [tmux, "-S", sock, "new-session", "-d", "-s", "s", "sleep 60"], check=True
        )
        subprocess.run(
            [
                tmux, "-S", sock, "new-window", "-d", "-t", "s:", "-n", "cheese-tunnel",
                f'HOME={home} CHEESE_TUNNEL_PORT={port} CHEESE_TUNNEL_URL=wss://x/y '
                f'exec sh "{home}/.cheese/cheese-tunnel-up"',
            ],
            check=True,
        )  # fmt: skip
        assert _await_listening(port), (
            "the helper did not come up in its own tmux window"
        )
        # The window's command has long returned by now; the helper must not have
        # gone down with it.
        time.sleep(2)
        assert _listening(port), (
            "the helper died with the window that started it — every turn on this "
            "screen now fails with ConnectionRefused and nothing can repair it"
        )
    finally:
        _kill_pidfile(home)
        subprocess.run([tmux, "-S", sock, "kill-server"], capture_output=True)


def _run_tunnel_up(home, port: int):
    return subprocess.run(
        ["sh", str(home / ".cheese" / "cheese-tunnel-up")],
        env={
            **os.environ,
            "HOME": str(home),
            "CHEESE_TUNNEL_PORT": str(port),
            "CHEESE_TUNNEL_URL": "wss://x/y",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_a_helper_it_already_started_is_adopted_rather_than_churned(tmp_path):
    """A screen is reused across turns, so this runs on every launch. Restarting
    a working helper each time would reset every in-flight connection."""
    home, _up = _tunnel_up_home(tmp_path)
    port = _free_port()
    pidf = home / ".cheese" / "cheese-tunnel.pid"
    try:
        _run_tunnel_up(home, port)
        assert _await_listening(port)
        first = pidf.read_text().strip()
        _run_tunnel_up(home, port)
        assert pidf.read_text().strip() == first, (
            "a healthy helper was restarted instead of adopted"
        )
        assert _listening(port)
    finally:
        _kill_pidfile(home)


def test_a_recorded_pid_that_is_alive_but_serves_no_port_is_replaced(tmp_path):
    """The recorded pid being alive proves only that SOME process holds that
    number — after a reboot, or on a box that has burnt through the pid space,
    that is a coincidence. Adopting on it would leave the port dead for the life
    of the screen, which is the failure this script exists to end."""
    home, _up = _tunnel_up_home(tmp_path)
    port = _free_port()
    claude = home / ".cheese"
    impostor = subprocess.Popen(["sh", "-c", "sleep 300"])
    try:
        # The state the box is actually found in: a pid that resolves, a stamp
        # that matches the helper on disk, and nothing listening.
        (claude / "cheese-tunnel.pid").write_text(f"{impostor.pid}\n")
        stamp = subprocess.run(
            ["cksum", str(claude / "cheese-tunnel.py")],
            capture_output=True,
            text=True,
        ).stdout.split()[0]
        (claude / "cheese-tunnel.stamp").write_text(f"{stamp}\n")
        assert not _listening(port)

        _run_tunnel_up(home, port)

        assert _await_listening(port), (
            "the port is still dead — an impostor pid was adopted as a live helper"
        )
        assert (claude / "cheese-tunnel.pid").read_text().strip() != str(impostor.pid)
    finally:
        _kill_pidfile(home)
        impostor.terminate()
        impostor.wait(timeout=5)


def test_the_helper_is_verified_by_the_dash_syntax_check_too():
    """`cheese-tunnel-up` runs under sh (dash on the machine images), and it is
    nested inside a heredoc inside an f-string — `bash -n` on the outer script
    does not parse it. Extracting it is the only way this is checked at all."""
    import subprocess

    from app.domain.agent.machine_launcher import CHEESE_TUNNEL_UP

    checked = subprocess.run(
        ["sh", "-n"], input=CHEESE_TUNNEL_UP, text=True, capture_output=True
    )
    assert checked.returncode == 0, checked.stderr


def test_the_launcher_adopts_that_ticket_as_the_model_credential():
    """It has to reach claude through the process environment, because the file
    that would otherwise carry it is in a home claude does not read."""
    script = device_launch.build_launch_script()

    assert '"$HOME/.claude/cheese-machine.token"' in script
    assert 'CLAUDE_CODE_OAUTH_TOKEN="$(cat "$HOME/.claude/cheese-machine.token")"' in (
        script
    )
    # Before claude starts, and before the tunnel helper's token is written.
    adopt = script.index("cheese-machine.token")
    assert adopt < script.index("cheese-tunnel.token.tmp")


def test_the_tunnel_password_stays_the_scoped_token():
    """The helper's token proves which project may open a tunnel — the scoped
    cheese token's job. Those two strings used to be equal, so reading either
    worked by accident; now CLAUDE_CODE_OAUTH_TOKEN is the machine's ccproxy
    ticket, and stamping it as the CONNECT password would 407 every tunnel."""
    script = device_launch.build_launch_script()
    start = script.index("<<TUNNELTOK\n") + len("<<TUNNELTOK\n")
    written = script[start : script.index("\nTUNNELTOK", start)]

    for connect in ["place-rc-token", ""]:
        result = subprocess.run(
            ["/bin/bash", "-c", "cat <<EOF\n" + written + "\nEOF"],
            env={
                "CHEESE_CONNECT_TOKEN": connect,
                "CHEESE_TOKEN": "hook-token",
                "CLAUDE_CODE_OAUTH_TOKEN": "machine-ticket",
            },
            check=True,
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == connect


# --- the read-only reconcile (#5: never touch the machine owner's files) ------
# CLAUDE_CONFIG_DIR keeps claude out of the owner's ~/.claude entirely, so the
# reconcile's only remaining job is EXTRACTION: on the machine-ticket path, read
# the machine's own ccproxy ticket out of wherever the machine keeps it and hand
# it to the launcher through a file. It must never write to the owner's files —
# the old write is what hijacked every claude the owner started by hand.


def _run_reconcile(
    tmp: str,
    *,
    settings: dict | None,
    credentials: str | None = None,
    backup: dict | None = None,
    env: dict | None = None,
) -> tuple[str, str | None, dict[str, float]]:
    """Run the shipped reconcile against an owner dir laid out per the args.

    Returns (stdout, handoff_or_None, mtimes) where mtimes maps each owner file
    to its post-run mtime — compared by the caller against pre-run to prove the
    reconcile never writes there.
    """
    import json
    import os
    import subprocess

    from app.domain.agent.harness.claude_code.device_launch import (
        CHEESE_SETTINGS_RECONCILE,
    )

    live = f"{tmp}/settings.json"
    if settings is not None:
        with open(live, "w") as handle:
            json.dump(settings, handle)
    if credentials is not None:
        with open(f"{tmp}/.credentials.json", "w") as handle:
            json.dump({"claudeAiOauth": {"accessToken": credentials}}, handle)
    if backup is not None:
        with open(live + ".cheese-orig", "w") as handle:
            json.dump(backup, handle)
    proc = subprocess.run(
        ["python3", "-", live, f"{tmp}/handoff.token"],
        input=CHEESE_SETTINGS_RECONCILE,
        text=True,
        capture_output=True,
        env={"PATH": "/usr/bin:/bin", **(env or {})},
        check=True,
    )
    handoff = None
    if os.path.exists(f"{tmp}/handoff.token"):
        with open(f"{tmp}/handoff.token") as handle:
            handoff = handle.read()
    mtimes = {
        name: os.path.getmtime(f"{tmp}/{name}")
        for name in ("settings.json", ".credentials.json", "settings.json.cheese-orig")
        if os.path.exists(f"{tmp}/{name}")
    }
    return proc.stdout.strip(), handoff, mtimes


_MT = {"CHEESE_MACHINE_TICKET": "1", "CLAUDE_CODE_OAUTH_TOKEN": "our.scoped.token"}


def test_the_reconcile_never_writes_the_owners_files(tmp_path):
    """The whole point of #5: the old reconcile REWROTE the owner's
    settings.json to be routed at all, which hijacked every claude the owner
    started by hand. With CLAUDE_CONFIG_DIR that premise is gone, so any write
    here is a regression — proven by content, not just mtime."""
    import hashlib
    import json

    tmp = str(tmp_path)
    settings = {"env": {"CLAUDE_CODE_OAUTH_TOKEN": "owner-ticket", "HTTPS_PROXY": "x"}}
    before = {}
    _, handoff, _ = _run_reconcile(
        tmp, settings=settings, credentials="store-ticket", backup={"env": {}}, env=_MT
    )
    for name in ("settings.json", ".credentials.json", "settings.json.cheese-orig"):
        with open(f"{tmp}/{name}", "rb") as handle:
            before[name] = hashlib.sha256(handle.read()).hexdigest()
    # Run it again — a second run over already-reconciled files is the shape
    # that used to rewrite; the content must be byte-identical afterwards.
    _, _, _ = _run_reconcile(tmp, settings=None, env=_MT)
    for name, digest in before.items():
        with open(f"{tmp}/{name}", "rb") as handle:
            assert hashlib.sha256(handle.read()).hexdigest() == digest, name
    assert handoff == "owner-ticket"
    assert json.loads(open(f"{tmp}/settings.json").read()) == settings


def test_off_the_machine_ticket_path_it_does_nothing_at_all(tmp_path):
    """Swap and gateway shapes are fully described by the process environment
    now; the reconcile must not even need the owner's settings.json to exist."""
    out, handoff, _ = _run_reconcile(
        str(tmp_path),
        settings=None,
        env={"CLAUDE_CODE_OAUTH_TOKEN": "our.scoped.token"},
    )
    assert out == "ok (env only)"
    assert handoff is None


def test_a_live_unrotated_ticket_wins(tmp_path):
    """MicroCloud seeds the ticket into settings.json's env, and a refresh lands
    there too — so a dot-less live value is the freshest copy and is extracted
    first (measured 2026-08-14: the backup's copy had already expired)."""
    _, handoff, _ = _run_reconcile(
        str(tmp_path),
        settings={"env": {"CLAUDE_CODE_OAUTH_TOKEN": "fresh-live-ticket"}},
        credentials="older-store-ticket",
        env=_MT,
    )
    assert handoff == "fresh-live-ticket"


def test_residue_of_the_old_writing_launcher_is_not_a_ticket(tmp_path):
    """A dotted live value is OUR scoped token, left by a launcher that wrote
    into this file — never a ticket. The store is the recovery source; without
    it the box looped forever on the swap path (measured 2026-08-15)."""
    _, handoff, _ = _run_reconcile(
        str(tmp_path),
        settings={"env": {"CLAUDE_CODE_OAUTH_TOKEN": "our.old.residue"}},
        credentials="store-ticket",
        env=_MT,
    )
    assert handoff == "store-ticket"


def test_a_box_with_no_settings_json_still_yields_its_store_ticket(tmp_path):
    """A login-style box (the dev box, a BYO machine) may keep its ticket ONLY
    in .credentials.json and have no settings.json at all."""
    _, handoff, _ = _run_reconcile(
        str(tmp_path), settings=None, credentials="store-ticket", env=_MT
    )
    assert handoff == "store-ticket"


def test_the_backup_serves_machines_the_old_launcher_touched(tmp_path):
    """A machine the WRITING launcher reconciled has a .cheese-orig holding the
    original ticket, and its live field may hold our residue with no separate
    credentials store. Last resort, and a dotted backup value is residue too."""
    _, handoff, _ = _run_reconcile(
        str(tmp_path),
        settings={"env": {"CLAUDE_CODE_OAUTH_TOKEN": "our.old.residue"}},
        backup={"env": {"CLAUDE_CODE_OAUTH_TOKEN": "original-ticket"}},
        env=_MT,
    )
    assert handoff == "original-ticket"


def test_no_ticket_anywhere_is_named_loudly_not_handed_off(tmp_path):
    """Without a ticket the launcher keeps the scoped token as bearer and the
    turn dies upstream as an opaque 401 — the reconcile's output line is the
    only thing that tells that apart from a routing failure."""
    out, handoff, _ = _run_reconcile(
        str(tmp_path),
        settings={"env": {"CLAUDE_CODE_OAUTH_TOKEN": "our.scoped.residue"}},
        env=_MT,
    )
    assert out == "no-machine-ticket"
    assert handoff is None


def test_the_launcher_exports_the_config_dir_and_passes_it_into_tmux():
    """CLAUDE_CONFIG_DIR is the isolation boundary itself: exported for the
    direct-exec path, and explicitly -e'd into the tmux session (tmux seeds a
    new session's env from the SERVER's global env, which predates this launch
    — same reasoning as the credential below it)."""
    script = device_launch.build_launch_script()
    assert 'export CLAUDE_CONFIG_DIR="$HOME/.claude"' in script
    # And the stale handoff from a previous launch is cleared before the
    # extraction runs, so an old ticket can never be exported by mistake.
    assert 'rm -f "$HOME/.claude/cheese-machine.token"' in script


# --- 运行环境预览: the preview helper shipped alongside the tunnel's -------------


def _launch_with_preview(**overrides):
    return _launch_with_tunnel(
        CHEESE_PREVIEW_URL="wss://gw.example/api/preview/tunnel", **overrides
    )


def test_the_preview_helper_and_its_token_are_written_every_launch():
    """Same reasoning as the tunnel helper's: it re-reads the token per
    connection, so rewriting the file is how a refreshed credential reaches a
    helper that is already running."""
    script = _launch_with_preview()
    assert 'cat > "$HOME/.cheese/cheese-preview.py"' in script
    # The real module, not a paraphrase of it.
    assert "class PortSource:" in script and "OP_WS_OPEN" in script
    # Written atomically and mode-restricted: it holds a scoped token.
    assert 'chmod 600 "$HOME/.cheese/cheese-preview.token.tmp"' in script


def test_a_deployment_without_a_preview_url_writes_and_runs_none_of_it():
    script = _launch_with_tunnel(CHEESE_PREVIEW_URL="")
    assert 'if [ -n "${CHEESE_PREVIEW_URL:-}" ]; then' in script


def test_the_preview_up_script_is_valid_shell_under_dash_too():
    """It runs under `sh` (dash on the machine images) and is nested inside a
    heredoc inside an f-string, so `bash -n` on the outer script never parses
    it — extracting it is the only way this is checked at all."""
    checked = subprocess.run(
        ["sh", "-n"],
        input=machine_launcher.CHEESE_PREVIEW_UP,
        text=True,
        capture_output=True,
    )
    assert checked.returncode == 0, checked.stderr


def test_a_machine_that_never_previews_anything_runs_no_helper(tmp_path):
    """The point of the port file: a preview costs a process only once somebody
    has asked for one. Starting the helper on every screen would put an idle
    python on every enrolled laptop for a feature most topics never use."""
    (tmp_path / ".cheese").mkdir()
    result = subprocess.run(
        ["sh", "-c", machine_launcher.CHEESE_PREVIEW_UP],
        env={
            "HOME": str(tmp_path),
            "PATH": os.environ["PATH"],
            "CHEESE_PREVIEW_URL": "wss://gw.example/api/preview/tunnel",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / ".cheese/cheese-preview.pid").exists()


def test_declaring_a_port_writes_it_on_the_machine(tmp_path):
    """`cheese serve` hands the port to this script and to nothing else. The
    file it lands in is the only address the helper will ever dial, so nothing
    the platform sends can move it — the whole reason the port is not a field on
    the wire. (No CHEESE_PREVIEW_URL here, so the helper itself never starts;
    what is under test is where the port ends up.)"""
    (tmp_path / ".cheese").mkdir()
    result = subprocess.run(
        [
            "sh",
            "-c",
            machine_launcher.CHEESE_PREVIEW_UP + "\n",
            "cheese-preview-up",
            "5173",
        ],
        env={"HOME": str(tmp_path), "PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / ".cheese/cheese-preview.port").read_text() == "5173\n"


def test_no_mcp_server_is_planted_in_a_sandbox():
    """The platform plants NO MCP server, on either delivery path.

    An earlier revision planted `mcp-server-fetch` to get a fetch path a
    deadline could reach. Measured against the same pages, that server extracts
    badly where it matters (a list-style page yields 622 characters against
    24,000+ from a converter that does not guess at "main content") and returns
    the raw page instead of an answer (33k tokens for one Wikipedia article,
    against tens of tokens from WebFetch's own summarisation). Claude Code's own
    tool description also tells the model to PREFER an MCP fetch tool whenever
    one exists, so planting one does not add a fallback — it replaces the better
    default. Fetching moves to a platform-side service instead.
    """
    script = device_launch.build_launch_script(
        sync_on_stop=True, system_prompt="", ca_pem=""
    )
    assert "mcpServers" not in _claude_json_from(script)

    from app.domain.agent.harness.claude_code.session_launch import (
        build_session_launch,
    )

    spec = build_session_launch(config_dir="/cfg", workdir="/work", system_prompt="x")
    container = json.loads(
        next(f.content for f in spec.files if f.name == ".claude.json")
    )
    assert "mcpServers" not in container


def test_a_summarisation_stream_that_stalls_is_bounded():
    """Keep the model-stream watchdog enabled alongside page-fetch handling."""
    assert device_launch.hooks_settings()["env"]["CLAUDE_ENABLE_STREAM_WATCHDOG"]


def _claude_json_from(script: str) -> dict:
    """The `.claude.json` the launch script writes, as the shell would leave it."""
    line = next(x for x in script.splitlines() if "hasCompletedOnboarding" in x)
    return json.loads(line.replace("$CHEESE_WORK", "/work"))


def test_repaired_webfetch_is_available_on_both_delivery_paths():
    """Both launch methods expose the native tool with its transport preload."""
    from app.domain.agent.harness.claude_code.cli import CLAUDE_BASE_CMD
    from app.domain.agent.harness.claude_code.session_launch import (
        build_session_launch,
    )

    assert "WebFetch" not in CLAUDE_BASE_CMD

    spec = build_session_launch(config_dir="/cfg", workdir="/work", system_prompt="x")
    settings = json.loads(
        next(f.content for f in spec.files if f.name == "settings.json")
    )
    assert "WebFetch" not in settings["permissions"]["deny"]
    assert shlex.split(spec.env["BUN_OPTIONS"]) == [
        "--preload=/cfg/webfetch_transport.cjs"
    ]


def _supervised_program(tmp_path):
    import shlex
    import sys

    home = tmp_path / "home"
    config = home / ".cheese"
    config.mkdir(parents=True)
    (config / "cheese-drain").write_text(_drain_body())
    spool = config / "spool"
    spool.mkdir()
    (config / "cheese-drain.env").write_text(f'CHEESE_HOOK_SPOOL="{spool}"\n')
    agent = tmp_path / "agent.py"
    agent.write_text(
        "import os, pathlib, sys, time\n"
        f"pathlib.Path({str(tmp_path / 'agent.pid')!r}).write_text(str(os.getpid()))\n"
        "if os.environ.get('WAIT_FOR_INPUT'):\n"
        "    print(sys.stdin.readline().strip(), flush=True)\n"
        "    sys.exit(7)\n"
        "time.sleep(60)\n"
    )
    body = device_launch.build_launch_script().split(
        "# The connector owns the terminal session.", 1
    )[1]
    script = tmp_path / "supervise.sh"
    script.write_text("set -e\n# The connector owns the terminal session." + body)
    env = {
        **os.environ,
        "HOME": str(home),
        "CLAUDE": f"{shlex.quote(sys.executable)} {shlex.quote(str(agent))}",
        "CHEESE_TUNNEL_URL": "",
        "CHEESE_PREVIEW_URL": "",
    }
    return script, env, config / "cheese-drain.pid", tmp_path / "agent.pid"


def _wait_file(path):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if path.exists():
            content = path.read_text().strip()
            if content:
                return int(content)
        time.sleep(0.02)
    pytest.fail(f"process never started: {path}")


def _assert_exited(pid):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)
    pytest.fail(f"process {pid} survived its owner")


@pytest.mark.parametrize("shell", ["sh", "dash"])
def test_supervisor_preserves_input_and_exit_status_and_reaps_drainer(tmp_path, shell):
    import shutil

    if shutil.which(shell) is None:
        pytest.skip(f"needs {shell}")
    script, env, drain_file, agent_file = _supervised_program(tmp_path)
    proc = subprocess.Popen(
        [shell, str(script)],
        env={**env, "WAIT_FOR_INPUT": "1"},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        drainer = _wait_file(drain_file)
        agent = _wait_file(agent_file)
        out, _ = proc.communicate("hello\n", timeout=5)
        assert out.strip() == "hello"
        assert proc.returncode == 7
        _assert_exited(drainer)
        _assert_exited(agent)
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)


def test_closing_real_tmux_ends_agent_and_drainer(tmp_path):
    import shlex
    import shutil
    import tempfile

    tmux = shutil.which("tmux")
    if tmux is None:
        pytest.skip("needs tmux")
    script, env, drain_file, agent_file = _supervised_program(tmp_path)
    with tempfile.TemporaryDirectory(prefix="cs-life-", dir="/tmp") as runtime:
        sock = runtime + "/t.sock"

        def run(*args):
            return subprocess.run(
                [tmux, "-S", sock, *args], env=env, capture_output=True, text=True
            )

        try:
            assert (
                run(
                    "new-session",
                    "-d",
                    "-s",
                    "screen",
                    f"exec sh {shlex.quote(str(script))}",
                ).returncode
                == 0
            )
            agent = _wait_file(agent_file)
            drainer = _wait_file(drain_file)
            assert (
                run("list-sessions", "-F", "#{session_name}").stdout.strip() == "screen"
            )
            assert run("kill-session", "-t", "=screen").returncode == 0
            _assert_exited(agent)
            _assert_exited(drainer)
        finally:
            run("kill-server")


def test_the_executor_client_is_told_the_config_dir_before_it_needs_it():
    """The client hands `claude` its config directory, and reads that directory
    from the environment rather than deriving it from where it was installed —
    the platform's files and the harness's config are two different places now,
    and only the launcher knows both. It is a plain environment lookup, so a
    launcher that invoked the client before exporting it would fail every
    executor-backed screen at startup, and nothing else in the script would say
    why. Nothing exercises that branch in-process: it runs on the machine."""
    script = device_launch.build_launch_script(
        remote_execution=True,
        system_prompt="x",
        model=None,
        resume_session_id=None,
        topic_id="t",
    )

    assert script.index('export CLAUDE_CONFIG_DIR="$HOME/.claude"') < script.index(
        'CLAUDE="python3 \\"$EXECUTOR_CLIENT\\" bootstrap'
    )
