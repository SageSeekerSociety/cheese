"""Device screen launcher: hooks settings + self-contained launch command."""

import os
import re
import subprocess
import time

from app.domain.agent import device_launch


def test_hooks_settings_wire_command_hook_to_forwarder():
    s = device_launch.hooks_settings()
    assert s["skipDangerousModePermissionPrompt"] is True
    # Every perception hook forwards via the `cheese-hook` COMMAND hook.
    events = ("SessionStart", "PreToolUse", "PostToolUse", "MessageDisplay", "Stop")
    for event in events:
        entry = s["hooks"][event][0]
        assert entry["hooks"][0] == {"type": "command", "command": "cheese-hook"}
    # The picker AskUserQuestion draws in the screen's terminal is unreachable
    # for a remote user too — denied in settings as well as on the launch line.
    assert s["permissions"]["deny"] == ["AskUserQuestion"]


def test_build_screen_launch_shapes_command_and_env():
    command, env, cheeselet = device_launch.build_screen_launch(
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
    assert "cheese-hook" in script
    assert "claude --dangerously-skip-permissions" in script
    # A remote screen is just as unreachable for AskUserQuestion's in-terminal
    # picker as the local pane is — same deny, carried on the launch line.
    assert "--disallowedTools AskUserQuestion" in script
    # Session name is derived from the work dir (per-topic isolation; a stale
    # session can't serve a different topic's tree).
    assert 'SESSION="cheese_$(printf' in script
    assert 'tmux new-session -d -s "$SESSION" -c "$CHEESE_WORK"' in script
    assert "CHEESE_HOOK_SPOOL" in script
    # The drainer deletes only on DURABLE acceptance (code:200 = live delivery or
    # server-side parking), with a 24h age cap for an unreachable backend.
    assert '"code":200' in script
    assert "-mmin +1440" in script
    # Env carries the hook wiring, home/work, model, and the gateway var.
    assert env["CHEESE_HOOK_URL"] == "http://h/sandbox/hooks/T"
    assert env["CHEESE_TOKEN"] == "scoped-tok"
    assert env["CHEESE_HOME"] == "/dev/home" and env["CHEESE_WORK"] == "/dev/work"
    assert env["CLAUDE_MODEL"] == "glm-5.2"
    assert env["ANTHROPIC_BASE_URL"] == "http://gw"
    # The gates are written by the launch script itself; nothing is passed for a
    # separate interpreter to read back.
    assert "CHEESE_CLAUDE_GATES" not in env
    # The minimal cheeselet only drives input (no state inference).
    assert "cheese.expose('prompt'" in cheeselet


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
    # The gates still land, with the work dir the shell resolved.
    assert 'cat > "$HOME/.claude.json"' in script
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
    command, _env, _cheeselet = device_launch.build_screen_launch(
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
    assert "cheese-drain" in wrapper
    assert wrapper.endswith("& exec $CLAUDE")
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
    # The loop honors the tether, so the revived window closes when claude goes.
    assert 'kill -0 "$CHEESE_DRAIN_TETHER"' in _drain_body()


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
    body = _drain_body()
    assert '. "$0.env"' in body
    assert body.index("while true") < body.index('. "$0.env"'), (
        "the config must be sourced inside the loop, not once at startup"
    )


def test_no_tmux_branch_keeps_the_drainer_in_claudes_own_tree():
    """Without tmux, claude stays in the launcher's process tree — a drainer
    backgrounded there genuinely shares its fate, so that shape stays."""
    script = device_launch.build_launch_script()
    no_tmux = script.split("\nelse\n", 1)[1]  # outer else only (inner is indented)
    assert 'sh "$HOME/.claude/cheese-drain"' in no_tmux
    assert 'eval "exec $CLAUDE"' in no_tmux


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


def test_drainer_keeps_an_unacknowledged_event(tmp_path):
    drain, spool, env = _write_drainer(tmp_path, curl_response='{"code":500}')
    event = spool / "1700000000.ev1"
    event.write_text('{"hook_event_name":"Stop"}')
    proc = subprocess.Popen(["sh", str(drain)], env=env)
    try:
        time.sleep(1.0)  # a couple of passes
        assert event.exists(), "an unacknowledged event must stay spooled"
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
    body = re.search(r'cat > "\$HOME/\.claude\.json" <<JSON\n(.*?)\nJSON', script, re.S)
    assert body, "the gates heredoc is not where the launcher writes it"
    parsed = _json.loads(body.group(1).replace("$CHEESE_WORK", "/w"))
    assert parsed["bypassPermissionsModeAccepted"] is True
    assert parsed["projects"]["/w"]["hasTrustDialogAccepted"] is True
