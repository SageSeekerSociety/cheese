"""Device screen launcher: hooks settings + self-contained launch command."""

import contextlib
import os
import re
import subprocess
import sys
import time
import uuid

import pytest

from app.domain.agent.harness.claude_code import device_launch


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
    # The picker AskUserQuestion draws in the screen's terminal is unreachable
    # for a remote user too — denied in settings as well as on the launch line.
    assert s["permissions"]["deny"] == ["AskUserQuestion"]


def test_build_screen_launch_shapes_command_and_env():
    command, env = device_launch.build_screen_launch(
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
    # Session name is derived from the work dir (per-topic isolation; a stale
    # session can't serve a different topic's tree).
    assert 'SESSION="cheese_$(printf' in script
    assert 'new-session -d -s "$SESSION" -c "$CHEESE_WORK"' in script
    assert "CHEESE_HOOK_SPOOL" in script
    # Env carries the hook wiring, home/work, model, and the gateway var.
    assert env["CHEESE_HOOK_URL"] == "http://h/sandbox/hooks/T"
    assert env["CHEESE_TOKEN"] == "scoped-tok"
    assert env["CHEESE_HOME"] == "/dev/home" and env["CHEESE_WORK"] == "/dev/work"
    assert env["CLAUDE_MODEL"] == "glm-5.2"
    assert env["ANTHROPIC_BASE_URL"] == "http://gw"
    # The gates are written by the launch script itself; nothing is passed for a
    # separate interpreter to read back.
    assert "CHEESE_CLAUDE_GATES" not in env


def test_the_room_bounds_how_deep_and_how_wide_its_work_can_go():
    # A piece of work IS a subagent of the room's session, so the room's two
    # structural limits are these env vars and nothing else enforces them.
    _, env = device_launch.build_screen_launch(
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
    commands = [
        line.strip()
        for line in script.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert not any(c.startswith("node ") for c in commands)
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
    command, _env = device_launch.build_screen_launch(
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


def test_drainer_lives_inside_the_claude_tmux_session():
    """The drainer used to be backgrounded in the OUTER launcher tree — the
    connector's process tree. A connector restart killed it while claude
    survived inside tmux: hooks kept spooling with no sender (84 piled up on a
    dev box while the platform read the turn as unresponsive). It must start
    inside the tmux session command itself, sharing claude's pane and fate."""
    script = device_launch.build_launch_script()
    # No drainer loop in the launcher's own tree on the tmux path.
    assert "( while true" not in script
    # The session command backgrounds the drainer, then execs claude — one
    # pane, one fate: the session dying takes both, the connector dying takes
    # neither.
    m = re.search(
        r'tmux new-session -d -s "\$SESSION" -c "\$CHEESE_WORK" \\\n\s+"(.+)"\n',
        script,
    )
    assert m, "new-session lost its command string"
    wrapper = m.group(1)
    # The drainer may appear inline or via the $DRAINCMD variable the script
    # defines right above (both expand to `sh .../cheese-drain`).
    assert "cheese-drain" in wrapper or "$DRAINCMD" in wrapper
    assert wrapper.endswith("& exec $ENVIRONMENT_CMD$CLAUDE")
    proc = subprocess.run(["sh", "-n"], input=script, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_adopt_rerun_revives_a_dead_drainer_but_never_doubles_a_live_one():
    """Re-running the launcher against a live session (the #369 adopt/reassert
    path) must be idempotent: a live drainer (its recorded pid answers) is left
    alone; a missing one (pre-fix session, crashed loop) is revived INSIDE the
    session, tethered to the claude pane so it cannot outlive claude and hold
    the session open."""
    script = device_launch.build_launch_script()
    assert 'cat "$HOME/.claude/cheese-drain.pid"' in script
    assert 'kill -0 "$DRAIN_PID"' in script
    assert 'tmux new-window -d -t "$SESSION" -n cheese-drain' in script
    assert "CHEESE_DRAIN_TETHER=$TETHER" in script


def test_drainer_config_is_rewritten_each_launch_and_read_each_pass():
    """An adopted drainer outlives the turn that started it, so it must deliver
    with the CURRENT turn's token/URL: the launcher rewrites the config file
    atomically on every run, and the loop re-sources it on every pass."""
    script = device_launch.build_launch_script()
    tmp = '"$HOME/.claude/cheese-drain.env.tmp"'
    assert f"cat > {tmp}" in script, "config must be staged to a tmp file"
    assert f"mv {tmp}" in script, "and moved into place atomically"
    for line in (
        'CHEESE_HOOK_SPOOL="$CHEESE_HOOK_SPOOL"',
        'CHEESE_HOOK_URL="$CHEESE_HOOK_URL"',
        'CHEESE_TOKEN="$CHEESE_TOKEN"',
    ):
        assert line in script


def test_no_tmux_branch_keeps_the_drainer_in_claudes_own_tree():
    """Without tmux, claude stays in the launcher's process tree — a drainer
    backgrounded there genuinely shares its fate, so that shape stays."""
    script = device_launch.build_launch_script()
    no_tmux = script.split("\nelse\n", 1)[1]  # outer else only (inner is indented)
    assert 'sh "$HOME/.claude/cheese-drain"' in no_tmux
    assert 'eval "exec $ENVIRONMENT_CMD$CLAUDE"' in no_tmux


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


def test_drainer_prunes_expired_events_without_resetting_sequence(tmp_path):
    drain, spool, env = _write_drainer(tmp_path, curl_response='{"code":500}')
    expired = [spool / "0000000000000000001.old", spool / ".n0000000000000000001"]
    sequence = spool / ".seq"
    for path in [*expired, sequence]:
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


# --- the inner claude must boot on THIS launch's token, not a frozen one ------
#
# A device's `claude` runs in a PERSISTENT inner tmux session on the connector's
# own private tmux server. The 407-that-outlives-a-re-mint has two layers:
#   1. tmux seeds a new session's env from the SERVER's GLOBAL env — frozen when
#      the server first started — for every var not in `update-environment`
#      (DISPLAY/SSH_* only). So a brand-new claude on an already-running server
#      inherits the token frozen weeks ago, not the one this turn minted. The
#      launcher now passes the credential per-key with `-e`, overriding the global.
#   2. A surviving session's claude reads its credential ONCE at startup; an
#      adopted-but-expired one keeps serving a dead token. The launcher stamps the
#      born-with expiry and retires a session whose credential has died.
# These tests drive the real generated shell (stub tmux, and a real tmux server
# for the frozen-global case) and assert both.


def _tmux_hosting_block() -> str:
    """The tmux-hosting branch of the launcher, standalone (its env is supplied by
    the caller instead of the full launcher's earlier setup)."""
    script = device_launch.build_launch_script()
    head = "if command -v tmux >/dev/null 2>&1; then\n"
    tail = '  exec tmux -S "$CHEESE_TMUX_SOCK" attach -t "$SESSION"\n'
    body = script.split(head, 1)[1].split(tail, 1)[0]
    return "set -e\n" + head + body + tail + "fi\n"


# The socket the stub/real tmux below stands for: the launcher reads it out of
# $TMUX, exactly as a pane of the connector's own tmux would.
STUB_SOCK = "/tmp/cheese-test-connector.sock"  # noqa: S108 — never bound, only named


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


def _run_block(env, expiry):
    env = {**env, "CHEESE_TOKEN_EXPIRES": str(expiry)}
    proc = subprocess.run(
        ["sh", "-c", _tmux_hosting_block()], env=env, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    return proc


def test_full_launcher_installs_platform_cli_without_network(tmp_path):
    home, env, _log = _stub_tmux_env(tmp_path)
    bindir = tmp_path / "bin"
    network = tmp_path / "network.calls"
    curl = bindir / "curl"
    curl.write_text(f'#!/bin/sh\necho attempted >> "{network}"\nexit 1\n')
    curl.chmod(0o755)
    claude = home / ".local/bin/claude"
    claude.parent.mkdir(parents=True)
    claude.write_text('#!/bin/sh\necho "2.1.261 (Claude Code)"\n')
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
    installed = home / ".claude/cheese"
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


def _tokexp_file(home):
    files = list((home / ".claude").glob("*.tokexp"))
    assert len(files) == 1, files
    return files[0]


def test_the_agent_session_never_lands_on_the_machine_owners_tmux_server(tmp_path):
    """A Hosted machine is someone's own laptop. If our claude lives in their
    DEFAULT tmux server then their `tmux kill-server` takes every agent on the
    box with it, our teardown takes their sessions, and `tmux ls` shows them our
    internals. So every command that hosts, adopts or attaches must name the
    connector's own socket — the one $TMUX handed this pane."""
    home, env, log = _stub_tmux_env(tmp_path)
    _run_block(env, expiry=int(time.time()) + 100_000)

    sockets = set(open(env["STUB_SOCKS"]).read().split())
    assert sockets == {STUB_SOCK}, (
        f"an inner tmux command went to a server we do not own: {sockets}"
    )
    on_default = [s for s in log.read_text().split() if s.startswith("default:")]
    assert not [s for s in on_default if s not in ("default:has-session",)], (
        f"the machine owner's server was used for more than a look: {on_default}"
    )


def test_a_session_left_on_the_machine_owners_server_is_retired(tmp_path):
    """An agent session sitting on the default server is ours wherever it came
    from, and it is not inert: it holds this topic's rendezvous socket, spool and
    work tree, so leaving it means a second claude answering for this topic. The
    launch takes it down instead of hosting alongside it."""
    home, env, log = _stub_tmux_env(tmp_path)
    open(env["STUB_OWNER_MARK"], "w").close()  # one is squatting there

    _run_block(env, expiry=int(time.time()) + 100_000)

    steps = log.read_text().split()
    assert "default:kill-session" in steps, "the misplaced session was left running"
    assert not os.path.exists(env["STUB_OWNER_MARK"])
    assert "new" in steps, "and this launch still hosts its own claude"


def test_a_session_whose_claude_died_is_not_adopted(tmp_path):
    """The connector's server keeps a pane after its program exits, so a claude
    that died leaves the session standing with a dead pane. Adopting it hosts
    nothing — every later turn would attach to a corpse and the topic would never
    get a claude again."""
    home, env, log = _stub_tmux_env(tmp_path)
    good = int(time.time()) + 100_000
    _run_block(env, expiry=good)  # create
    _run_block({**env, "STUB_PANES": "1"}, expiry=good)  # its claude has since died

    steps = log.read_text().split()
    assert "kill" in steps, "a session whose pane is dead must be retired"
    assert steps.count("new") == 2, "and replaced by a live claude"


def test_a_fresh_inner_session_records_the_launch_token_expiry(tmp_path):
    """First launch (no session yet): claude starts and the launcher records the
    expiry of the token it was born with, so a later launch can judge it."""
    home, env, log = _stub_tmux_env(tmp_path)
    exp = int(time.time()) + 100_000
    _run_block(env, expiry=exp)
    assert "new" in log.read_text().split()  # a session was created
    assert _tokexp_file(home).read_text().strip() == str(exp)


def test_a_stale_inner_session_is_retired_and_relaunched_with_the_fresh_expiry(
    tmp_path,
):
    """The bug: a surviving session whose baked credential has expired is adopted,
    so the fresh token never runs. Now it is killed and replaced, and the NEW
    launch's expiry is recorded — not the reused dead one."""
    home, env, log = _stub_tmux_env(tmp_path)
    _run_block(env, expiry=int(time.time()) + 100_000)  # create the session
    # Its baked token has since expired (a pre-#385 1h token, or a >TTL-old one).
    _tokexp_file(home).write_text(str(int(time.time()) - 100))
    fresh = int(time.time()) + 900_000
    _run_block(env, expiry=fresh)  # relaunch

    steps = log.read_text().split()
    assert "kill" in steps, "a stale-credential session must be retired"
    assert steps.count("new") == 2, "and replaced by a fresh claude"
    assert _tokexp_file(home).read_text().strip() == str(fresh), (
        "the relaunch must record the FRESH expiry, not the reused dead one"
    )


def test_a_valid_inner_session_is_adopted_without_relaunch(tmp_path):
    """No churn: a session whose baked token is still good is adopted unchanged —
    an in-flight turn on a live credential is never interrupted."""
    home, env, log = _stub_tmux_env(tmp_path)
    good = int(time.time()) + 100_000
    _run_block(env, expiry=good)  # create
    _run_block(env, expiry=int(time.time()) + 900_000)  # relaunch, token still good

    steps = log.read_text().split()
    assert "kill" not in steps, "a still-valid session must not be killed"
    assert steps.count("new") == 1, "and must not be relaunched"
    assert _tokexp_file(home).read_text().strip() == str(good), "expiry left intact"


def test_explicit_environment_application_restarts_a_process_without_a_receipt(
    tmp_path,
):
    home, env, log = _stub_tmux_env(tmp_path)
    good = int(time.time()) + 100_000
    _run_block(env, expiry=good)
    marker = home / ".claude/environment-restart"
    marker.touch()
    _run_block(env, expiry=good)
    assert log.read_text().split().count("new") == 2
    assert not marker.exists()


def test_create_passes_the_fresh_credential_explicitly_via_dash_e(tmp_path):
    """The new session must be handed THIS launch's token with -e, not left to
    inherit it — inheritance is exactly what pulls the stale frozen-global token."""
    home, env, log = _stub_tmux_env(tmp_path)
    env = {
        **env,
        "CLAUDE_CODE_OAUTH_TOKEN": "fresh-oauth-tok",
        "HTTPS_PROXY": "http://cheese:fresh-oauth-tok@proxy:8080",
        "CHEESE_TOPIC": "topic-abc",
    }
    _run_block(env, expiry=int(time.time()) + 100_000)
    args = open(env["STUB_ARGS"]).read().splitlines()
    assert "-e" in args, "the session must be created with explicit env"
    assert "CLAUDE_CODE_OAUTH_TOKEN=fresh-oauth-tok" in args
    assert "HTTPS_PROXY=http://cheese:fresh-oauth-tok@proxy:8080" in args
    # per-topic attribution must be explicit too (the alive probe keys on it)
    assert "CHEESE_TOPIC=topic-abc" in args


def _tmux_ge_30() -> bool:
    import shutil

    if not shutil.which("tmux"):
        return False
    out = subprocess.run(["tmux", "-V"], capture_output=True, text=True).stdout
    m = re.search(r"(\d+)\.(\d+)", out)
    return bool(m) and (int(m.group(1)), int(m.group(2))) >= (3, 0)


@pytest.mark.skipif(not _tmux_ge_30(), reason="needs a real tmux >= 3.0")
def test_environment_prepares_once_on_attach_and_runs_startup_after_reset(tmp_path):
    import json
    import shutil
    import sys

    from app.domain.agent import environment_runner
    from app.domain.project.environment import EnvironmentConfig

    socket = f"/tmp/ce{os.getpid()}.sock"  # noqa: S108 — test-owned tmux socket
    home = tmp_path / "home"
    helpers = home / ".claude"
    helpers.mkdir(parents=True)
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    shutil.copy(environment_runner.__file__, helpers / "cheese-environment.py")
    (helpers / "cheese-drain").write_text("#!/bin/sh\nexit 0\n")
    agent = tmp_path / "fake agent.sh"
    agent.write_text(
        "#!/bin/sh\ntest -f startup || exit 1\necho agent >> agents\nexec sleep 60\n"
    )
    agent.chmod(0o755)
    config = EnvironmentConfig(
        setup_script="echo setup >> setup", startup_script="echo startup >> startup"
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
        script = device_launch.build_launch_script()
        prefix = (
            'ENVIRONMENT_CMD=""'
            + script.split('ENVIRONMENT_CMD=""', 1)[1].split('[ -s "$CHEESE_SP" ]', 1)[
                0
            ]
        )
        subprocess.run(
            ["sh", "-c", prefix + _tmux_hosting_block()],
            env=env,
            capture_output=True,
            timeout=10,
        )

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
        assert (work / "startup").read_text() == "startup\n"
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
        assert (work / "startup").read_text() == "startup\nstartup\n"
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
    (home / ".claude").mkdir(parents=True)
    work = home / "work"
    work.mkdir()
    (home / ".claude" / "cheese-drain").write_text("#!/bin/sh\nsleep 3\n")
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
            "CHEESE_HOOK_SPOOL": f"{home}/.claude/cheese-spool",
            "CHEESE_TOKEN_EXPIRES": str(int(time.time()) + 100_000),
            # What a pane of the connector's own tmux sees. The launcher reads
            # the socket out of it, so the seeded server IS the one it hosts in
            # — no wrapper pinning it there.
            "TMUX": f"{sock},1,0",
        }
        subprocess.run(
            ["sh", "-c", _tmux_hosting_block()],
            env=env,
            capture_output=True,
            text=True,
        )  # ends in `exec tmux attach` (fails fast, no tty) — the session is up
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
        assert got[3:] == [f"{home}/.claude/cheese-spool"], (
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
    command, _env = device_launch.build_screen_launch(
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
    assert 'cat > "$HOME/.claude/cheese-tunnel.py"' in script
    # The real module, not a paraphrase of it.
    assert "def open_tunnel(" in script and "Sec-WebSocket-Key" in script
    # Written atomically and mode-restricted: it holds a spendable token.
    assert 'chmod 600 "$HOME/.claude/cheese-tunnel.token.tmp"' in script
    assert (
        'mv "$HOME/.claude/cheese-tunnel.token.tmp" "$HOME/.claude/cheese-tunnel.token"'
        in script
    )


def test_the_helper_starts_inside_the_session_and_before_claude():
    """Two properties, one line. INSIDE: backgrounded in the launcher's own tree
    it would die with the connector while claude survives in tmux. BEFORE:
    claude reads HTTPS_PROXY once and calls out immediately, so a helper that is
    still binding loses that race and the screen boots unauthenticated."""
    script = _launch_with_tunnel()
    assert "$TUP$PUP $DRAINCMD & exec $ENVIRONMENT_CMD$CLAUDE" in script
    assert 'TUP="sh \\"$HOME/.claude/cheese-tunnel-up\\" >/dev/null 2>&1;"' in script
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
    assert 'TUP=""' in script
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
    assert 'cksum "$HOME/.claude/cheese-tunnel.py"' in script
    # Adoption is conditional on it, and the mismatch path kills.
    assert '[ "$WANT" = "$HAVE" ]' in script
    assert 'kill "$PID"' in script


def _tunnel_up_home(tmp_path):
    """A HOME laid out the way the launcher leaves one, with a stub helper that
    binds its --port and then sits there — the only thing about the real helper
    this script cares about."""
    from app.domain.agent.harness.claude_code.device_launch import CHEESE_TUNNEL_UP

    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "cheese-tunnel.py").write_text(
        "import socket, sys, time\n"
        "port = int(sys.argv[sys.argv.index('--port') + 1])\n"
        "s = socket.socket()\n"
        "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        "s.bind(('127.0.0.1', port))\n"
        "s.listen(5)\n"
        "print('tunnel listening on 127.0.0.1:%d' % port, flush=True)\n"
        "time.sleep(300)\n"
    )
    (home / ".claude" / "cheese-tunnel.token").write_text("")
    up = home / ".claude" / "cheese-tunnel-up"
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
        pid = int((home / ".claude" / "cheese-tunnel.pid").read_text().strip())
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
                f'exec sh "{home}/.claude/cheese-tunnel-up"',
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
        ["sh", str(home / ".claude" / "cheese-tunnel-up")],
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
    pidf = home / ".claude" / "cheese-tunnel.pid"
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
    claude = home / ".claude"
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

    from app.domain.agent.harness.claude_code.device_launch import CHEESE_TUNNEL_UP

    checked = subprocess.run(
        ["sh", "-n"], input=CHEESE_TUNNEL_UP, text=True, capture_output=True
    )
    assert checked.returncode == 0, checked.stderr


def test_a_session_born_on_a_different_contract_is_retired():
    """claude reads settings.json ONCE at startup and a screen is reused across
    turns, so a shipped change to WHICH ticket it carries reaches the file and
    never reaches the process. Measured 2026-08-14: the file said one thing and
    the running claude kept failing on the other, with nothing to indicate why."""
    script = _launch_with_tunnel()
    assert "$SESSION.cfg" in script
    assert '"$REAL_HOME/.claude/settings.json"' in script
    # The contract is the settings file AND the ticket the launcher exports; the
    # ticket does not live in that file, so checksumming the file alone would
    # miss a rotation entirely. See the test below.
    assert "cksum" in script
    # Both reasons retire, and neither is allowed to mask the other.
    assert "RETIRE=1" in script
    assert script.count("RETIRE=1") >= 2


def test_surviving_inner_session_detects_an_agent_edit(tmp_path):
    import os
    import subprocess
    import time

    script = device_launch.build_launch_script()
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text("{}")
    (config_dir / "cheese-machine.token").write_text("unchanged-ticket")
    (config_dir / "agent-configuration").write_text("original-config")
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "REAL_HOME": str(tmp_path),
        "SESSION": "test-session",
        "TOKEXP": str(int(time.time()) + 3600),
    }
    start = script.rindex('    cat "$REAL_HOME/.claude/settings.json"')
    record = script[start : script.index("    # Hand THIS launch", start)]
    subprocess.run(["bash", "-c", record], env=env, check=True)
    start = script.index('    CFGF="$HOME/.claude/$SESSION.cfg"')
    gate = script[start : script.index("    # The connector's server", start)]
    for config, retired in [("original-config", "0"), ("edited-config", "1")]:
        (config_dir / "agent-configuration").write_text(config)
        result = subprocess.run(
            ["bash", "-c", gate + '\nprintf "%s" "$RETIRE"'],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        assert result.stdout == retired


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


def test_a_changed_machine_ticket_retires_the_session_that_baked_the_old_one():
    """claude reads its model credential ONCE at startup, and the ticket is
    exported by the launcher rather than living in the settings.json the retire
    gate checksums — so a rotated ticket would leave that file byte-identical
    and the running claude holding a dead credential forever. Both sides of the
    comparison have to include it, or the gate compares the wrong thing on one
    of them and retires on every single launch."""
    script = device_launch.build_launch_script()

    both = [
        line
        for line in script.splitlines()
        if "cheese-machine.token" in line and "cksum" in script
    ]
    assert len(both) >= 2, "the ticket joins the checksum when read AND when recorded"
    # And the old single-file form is gone from both, or one side would compare
    # a checksum of different bytes and never match.
    assert 'cksum "$REAL_HOME/.claude/settings.json"' not in script


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
    assert '"CLAUDE_CONFIG_DIR=$CLAUDE_CONFIG_DIR"' in script
    # And the stale handoff from a previous launch is cleared before the
    # extraction runs, so an old ticket can never be exported by mistake.
    assert 'rm -f "$HOME/.claude/cheese-machine.token"' in script


def test_a_transient_create_failure_fails_loudly_not_into_the_fallback(tmp_path):
    """#427: the no-`-e` fallback is for pre-3.0 tmux ONLY. Any other create
    failure must surface as a launcher failure (→ a visible screen-setup error
    on the turn), never silently retry without per-session env."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    work = home / "work"
    work.mkdir()
    (home / ".claude" / "cheese-drain").write_text("#!/bin/sh\nexit 0\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "tmux.calls"
    stub = bindir / "tmux"
    stub.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-S" ]; then shift 2; fi\n'
        f'echo "$@" >> "{calls}"\n'
        'case "$1" in\n'
        "  has-session) exit 1 ;;\n"
        '  new-session) echo "create failed: server error" >&2; '
        "exit 1 ;;\n"
        "esac\nexit 0\n"
    )
    stub.chmod(0o755)
    result = subprocess.run(
        ["sh", "-c", _tmux_hosting_block()],
        env={
            "PATH": f"{bindir}:/usr/bin:/bin",
            "HOME": str(home),
            "CHEESE_WORK": str(work),
            "CLAUDE": "true",
            "CHEESE_TOKEN_EXPIRES": str(int(time.time()) + 100_000),
            "TMUX": f"{STUB_SOCK},1,0",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, "a non-version create failure must fail the launch"
    assert "cheese-launch: tmux new-session failed" in result.stderr
    body = calls.read_text()
    assert body.count("new-session") == 1, (
        f"the no--e fallback ran on a non-version failure: {body}"
    )


def test_an_old_tmux_without_dash_e_still_gets_the_fallback(tmp_path):
    """The one failure the fallback exists for: a pre-3.0 tmux rejecting `-e`
    (usage/unknown-flag output) still launches the screen, with the sourced env
    file carrying the full environment (#434)."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    work = home / "work"
    work.mkdir()
    (home / ".claude" / "cheese-drain").write_text("#!/bin/sh\nexit 0\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "tmux.calls"
    stub = bindir / "tmux"
    stub.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-S" ]; then shift 2; fi\n'
        f'echo "$@" >> "{calls}"\n'
        'case "$1" in\n'
        "  has-session) exit 1 ;;\n"
        "  new-session)\n"
        f'    if ! grep -q "fallback-done" "{calls}" 2>/dev/null '
        '&& echo "$@" | grep -q -- " -e "; then\n'
        '      echo "usage: new-session [-AdDEPX] ..." >&2; exit 1\n'
        "    fi\n"
        f'    echo fallback-done >> "{calls}"\n'
        "    exit 0 ;;\n"
        "  attach) exit 0 ;;\n"
        "esac\nexit 0\n"
    )
    stub.chmod(0o755)
    result = subprocess.run(
        ["sh", "-c", _tmux_hosting_block()],
        env={
            "PATH": f"{bindir}:/usr/bin:/bin",
            "HOME": str(home),
            "CHEESE_WORK": str(work),
            "CLAUDE": "true",
            "CHEESE_TOKEN_EXPIRES": str(int(time.time()) + 100_000),
            "TMUX": f"{STUB_SOCK},1,0",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "fallback-done" in calls.read_text()


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
    assert 'cat > "$HOME/.claude/cheese-preview.py"' in script
    # The real module, not a paraphrase of it.
    assert "class PortSource:" in script and "OP_WS_OPEN" in script
    # Written atomically and mode-restricted: it holds a scoped token.
    assert 'chmod 600 "$HOME/.claude/cheese-preview.token.tmp"' in script


def test_a_deployment_without_a_preview_url_writes_and_runs_none_of_it():
    script = _launch_with_tunnel(CHEESE_PREVIEW_URL="")
    assert 'PUP=""' in script
    assert 'if [ -n "${CHEESE_PREVIEW_URL:-}" ]; then' in script


def test_the_preview_up_script_is_valid_shell_under_dash_too():
    """It runs under `sh` (dash on the machine images) and is nested inside a
    heredoc inside an f-string, so `bash -n` on the outer script never parses
    it — extracting it is the only way this is checked at all."""
    checked = subprocess.run(
        ["sh", "-n"],
        input=device_launch.CHEESE_PREVIEW_UP,
        text=True,
        capture_output=True,
    )
    assert checked.returncode == 0, checked.stderr


def test_a_machine_that_never_previews_anything_runs_no_helper(tmp_path):
    """The point of the port file: a preview costs a process only once somebody
    has asked for one. Starting the helper on every screen would put an idle
    python on every enrolled laptop for a feature most topics never use."""
    (tmp_path / ".claude").mkdir()
    result = subprocess.run(
        ["sh", "-c", device_launch.CHEESE_PREVIEW_UP],
        env={
            "HOME": str(tmp_path),
            "PATH": os.environ["PATH"],
            "CHEESE_PREVIEW_URL": "wss://gw.example/api/preview/tunnel",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / ".claude/cheese-preview.pid").exists()


def test_declaring_a_port_writes_it_on_the_machine(tmp_path):
    """`cheese serve` hands the port to this script and to nothing else. The
    file it lands in is the only address the helper will ever dial, so nothing
    the platform sends can move it — the whole reason the port is not a field on
    the wire. (No CHEESE_PREVIEW_URL here, so the helper itself never starts;
    what is under test is where the port ends up.)"""
    (tmp_path / ".claude").mkdir()
    result = subprocess.run(
        [
            "sh",
            "-c",
            device_launch.CHEESE_PREVIEW_UP + "\n",
            "cheese-preview-up",
            "5173",
        ],
        env={"HOME": str(tmp_path), "PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / ".claude/cheese-preview.port").read_text() == "5173\n"
