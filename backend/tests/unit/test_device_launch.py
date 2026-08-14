"""Device screen launcher: hooks settings + self-contained launch command."""

import os
import re
import subprocess
import time

import pytest

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


# --- the inner claude must boot on THIS launch's token, not a frozen one ------
#
# A device's `claude` runs in a PERSISTENT inner tmux session on the box's default
# tmux server. The 407-that-outlives-a-re-mint has two layers:
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
    after = script.split("  unset TMUX\n", 1)[1]
    body = after.split('  exec tmux attach -t "$SESSION"\n', 1)[0]
    return "set -e\nunset TMUX\n" + body + 'exec tmux attach -t "$SESSION"\n'


def _stub_tmux_env(tmp_path):
    """A home + a stub `tmux` that records kill/new/window to a log and toggles a
    has-session marker, so a run's session-lifecycle decisions are observable."""
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
        'cmd="$1"; shift\n'
        'case "$cmd" in\n'
        '  has-session) [ -f "$STUB_MARK" ] ;;\n'
        '  kill-session) printf \'kill\\n\' >> "$STUB_LOG"; rm -f "$STUB_MARK" ;;\n'
        "  new-session) printf 'new\\n' >> \"$STUB_LOG\";"
        ' printf \'%s\\n\' "$@" >> "$STUB_ARGS"; : > "$STUB_MARK" ;;\n'
        "  new-window) printf 'window\\n' >> \"$STUB_LOG\" ;;\n"
        "  list-panes) printf '12345\\n' ;;\n"
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
        "STUB_LOG": str(log),
        "STUB_MARK": str(mark),
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


def _tokexp_file(home):
    files = list((home / ".claude").glob("*.tokexp"))
    assert len(files) == 1, files
    return files[0]


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
    fake_claude.write_text(
        f'#!/bin/sh\nprintenv CLAUDE_CODE_OAUTH_TOKEN > "{token_out}"\nsleep 3\n'
    )
    fake_claude.chmod(0o755)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    # A tmux that pins every launcher call to our private test server (which we
    # pre-seed with a STALE global token, standing in for the box's old server).
    (bindir / "tmux").write_text(f'#!/bin/sh\nexec "{real_tmux}" -S "{sock}" "$@"\n')
    (bindir / "tmux").chmod(0o755)
    try:
        subprocess.run(
            [real_tmux, "-S", sock, "new-session", "-d", "-s", "seed", "sleep 60"],
            env={**os.environ, "CLAUDE_CODE_OAUTH_TOKEN": "STALE-frozen-token"},
            check=True,
        )
        env = {
            **os.environ,
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "HOME": str(home),
            "CHEESE_WORK": str(work),
            "CLAUDE": f"sh {fake_claude}",
            "CLAUDE_CODE_OAUTH_TOKEN": "FRESH-live-token",
            "CHEESE_TOKEN_EXPIRES": str(int(time.time()) + 100_000),
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
        assert token_out.read_text().strip() == "FRESH-live-token", (
            "the new claude booted on the STALE server-global token, not this "
            "launch's fresh one — the frozen-global 407 is not fixed"
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
    command, _env, _cheeselet = device_launch.build_screen_launch(
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
    assert "$TUP $DRAINCMD & exec $CLAUDE" in script
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


def test_the_helper_is_verified_by_the_dash_syntax_check_too():
    """`cheese-tunnel-up` runs under sh (dash on the machine images), and it is
    nested inside a heredoc inside an f-string — `bash -n` on the outer script
    does not parse it. Extracting it is the only way this is checked at all."""
    import subprocess

    from app.domain.agent.device_launch import CHEESE_TUNNEL_UP

    checked = subprocess.run(
        ["sh", "-n"], input=CHEESE_TUNNEL_UP, text=True, capture_output=True
    )
    assert checked.returncode == 0, checked.stderr


def _reconcile(existing: dict, env: dict) -> dict:
    """Run the shipped reconcile against a settings.json, as it runs on a
    machine: same source, same os.environ contract."""
    import json
    import subprocess
    import tempfile

    from app.domain.agent.device_launch import CHEESE_SETTINGS_RECONCILE

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(existing, handle)
        path = handle.name
    subprocess.run(
        ["python3", "-", path],
        input=CHEESE_SETTINGS_RECONCILE,
        text=True,
        capture_output=True,
        env={"PATH": "/usr/bin:/bin", **env},
        check=True,
    )
    with open(path) as handle:
        return json.load(handle)


def test_the_pass_through_ticket_comes_from_the_backup_not_the_live_file():
    """The live file's token is whatever the LAST launch left there, and every
    launch before the tunnel existed overwrote it with ours. So reading the live
    file preserves our own stale scoped token and changes nothing — measured
    2026-08-14, that is exactly what happened. The backup is written once,
    before anything was overwritten, and is the only place the machine's
    original ticket survives."""
    import json
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        live = f"{tmp}/settings.json"
        with open(live, "w") as handle:
            # what a previous launch left: OUR token, not the machine's
            json.dump({"env": {"CLAUDE_CODE_OAUTH_TOKEN": "old.scoped.token"}}, handle)
        with open(live + ".cheese-orig", "w") as handle:
            json.dump({"env": {"CLAUDE_CODE_OAUTH_TOKEN": "machine-ticket"}}, handle)

        import subprocess

        from app.domain.agent.device_launch import CHEESE_SETTINGS_RECONCILE

        subprocess.run(
            ["python3", "-", live],
            input=CHEESE_SETTINGS_RECONCILE,
            text=True,
            capture_output=True,
            env={
                "PATH": "/usr/bin:/bin",
                "CLAUDE_CODE_OAUTH_TOKEN": "our-scoped-token",
                "HTTPS_PROXY": "http://127.0.0.1:8445",
                "CHEESE_TUNNEL_URL": "wss://gw/api/llm/tunnel",
            },
            check=True,
        )
        with open(live) as handle:
            env = json.load(handle)["env"]

    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "machine-ticket"


def test_the_pass_through_path_keeps_the_machines_own_ticket():
    """On the tunnel the meter forwards the bearer untouched, because ccproxy
    only honours a machine's ticket over that machine's own identity. Asserting
    ours would send a scoped cheese token to Anthropic — measured 2026-08-14,
    that is `401 OAuth access token has been revoked` with the whole chain up."""
    result = _reconcile(
        {"env": {"CLAUDE_CODE_OAUTH_TOKEN": "machine-ticket", "HTTPS_PROXY": "old"}},
        {
            "CLAUDE_CODE_OAUTH_TOKEN": "our-scoped-token",
            "HTTPS_PROXY": "http://127.0.0.1:8445",
            "CHEESE_TUNNEL_URL": "wss://gw/api/llm/tunnel",
        },
    )
    assert result["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "machine-ticket"
    # Everything else is still ours — the route has to point at the helper.
    assert result["env"]["HTTPS_PROXY"] == "http://127.0.0.1:8445"


def test_the_swap_path_still_asserts_our_ticket():
    """No tunnel: the meter replaces the bearer with the credential the host
    holds, so the image's own would only be a second thing to keep in sync."""
    result = _reconcile(
        {"env": {"CLAUDE_CODE_OAUTH_TOKEN": "machine-ticket", "HTTPS_PROXY": "old"}},
        {
            "CLAUDE_CODE_OAUTH_TOKEN": "our-scoped-token",
            "HTTPS_PROXY": "http://cheese:tok@172.17.0.1:8444",
        },
    )
    assert result["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "our-scoped-token"


def test_a_session_born_on_a_different_contract_is_retired():
    """claude reads settings.json ONCE at startup and a screen is reused across
    turns, so a shipped change to WHICH ticket it carries reaches the file and
    never reaches the process. Measured 2026-08-14: the file said one thing and
    the running claude kept failing on the other, with nothing to indicate why."""
    script = _launch_with_tunnel()
    assert "$SESSION.cfg" in script
    assert 'cksum "$REAL_HOME/.claude/settings.json"' in script
    # Both reasons retire, and neither is allowed to mask the other.
    assert "RETIRE=1" in script
    assert script.count("RETIRE=1") >= 2
