"""The device screen's prompt-delivery wiring: a rendezvous socket, a pinned
binary, and a version floor that refuses to degrade quietly.

What these guard, in one line each: a socket path short enough to bind, an
adopted screen and a fresh one agreeing on the same token, and an old `claude`
failing the launch instead of falling back to typing into a terminal.
"""

import subprocess
from pathlib import Path

from app.domain.agent.harness.claude_code import device_launch

TOPIC = "c43d2e12-6d4f-436d-b436-05278a879f81"


def test_socket_path_fits_the_kernel_limit():
    sock, token_file = device_launch.rendezvous_paths(TOPIC)
    # sockaddr_un.sun_path is 104 bytes on macOS / 108 on Linux, and bind fails
    # with a bare EINVAL that reads like a bug in our code. The isolated home
    # alone spends ~105, which is exactly why these live in /tmp.
    assert len(sock) < 100, sock
    assert len(token_file) < 100, token_file
    assert sock.endswith(".sock") and token_file.endswith(".token")


def test_socket_path_is_per_topic_and_stable():
    a, _ = device_launch.rendezvous_paths(TOPIC)
    b, _ = device_launch.rendezvous_paths("73ac850d-3759-415c-b112-c8e06b20aefd")
    assert a != b
    # Stable across calls: the connector dials the path the launcher bound, and
    # an adopted screen must resolve to the same one a fresh launch would.
    assert device_launch.rendezvous_paths(TOPIC)[0] == a


def test_screen_env_carries_the_socket_only_with_a_topic():
    _, env, _ = device_launch.build_screen_launch(
        hook_url="http://h",
        hook_token="t",
        home_dir="/h",
        work_dir="/w",
        topic_id=TOPIC,
    )
    assert env[device_launch.ENV_RV_SOCK] == device_launch.rendezvous_paths(TOPIC)[0]
    assert env[device_launch.ENV_RV_TOKEN_FILE].endswith(".token")

    # No topic (a probe / bare screen) means no delivery socket to name; a made-up
    # path would just be a file nobody binds.
    _, bare, _ = device_launch.build_screen_launch(
        hook_url="http://h",
        hook_token="t",
        home_dir="/h",
        work_dir="/w",
    )
    assert device_launch.ENV_RV_SOCK not in bare
    assert device_launch.ENV_RV_TOKEN_FILE not in bare


def test_launcher_arms_claude_codes_own_env_trio():
    script = device_launch.build_launch_script()
    # The gate: without CLAUDE_BG_BACKEND=daemon the session binds nothing, and
    # every prompt would fail to deliver with no obvious reason why.
    assert "CLAUDE_BG_BACKEND=daemon" in script
    assert 'CLAUDE_BG_RENDEZVOUS_SOCK="$CHEESE_RV_SOCK"' in script
    assert "CLAUDE_BG_RV_AUTH=" in script
    assert (
        "export CLAUDE_BG_BACKEND CLAUDE_BG_RENDEZVOUS_SOCK CLAUDE_BG_RV_AUTH" in script
    )
    # tmux seeds a new session from the SERVER's frozen global env, so the trio
    # must also travel on the explicit -e list (#409/#433 class of bug).
    assert '"CLAUDE_BG_RENDEZVOUS_SOCK=$CLAUDE_BG_RENDEZVOUS_SOCK"' in script


def test_launcher_reuses_an_existing_token_file():
    script = device_launch.build_launch_script()
    # Regenerating on every launch would break an ADOPTED claude, which keeps the
    # token it booted with: the file is the one copy both sides read.
    assert '[ ! -s "$CHEESE_RV_TOKEN_FILE" ]' in script
    assert '[ ! -O "$CHEESE_RV_TOKEN_FILE" ]' in script  # and not someone else's
    assert "umask 077" in script


def test_launcher_refuses_an_old_claude_instead_of_falling_back():
    script = device_launch.build_launch_script()
    assert device_launch.CLAUDE_MIN_VERSION in script
    assert "sort -V" in script
    # The failure must be an exit, not a warning: a silent downgrade to pasting
    # is the exact behaviour this replaced.
    assert "exit 1" in script
    assert "claude install stable" in script  # and it says how to fix it


def test_version_floor_compares_semver_the_right_way_round():
    """The floor is a shell expression, so test it as one.

    Getting `sort -V` backwards is a one-character mistake that would either
    reject every device or accept every device — and neither shows up until a
    real screen is launched.
    """
    floor = device_launch.CLAUDE_MIN_VERSION
    expr = 'test "$(printf \'%s\\n%s\\n\' "$MIN" "$V" | sort -V | head -n 1)" = "$MIN"'
    cases = {
        "2.1.220": False,  # the version the dev box was stuck on
        "2.1.223": False,
        "2.1.224": True,  # the floor itself
        "2.1.233": True,
        "2.2.0": True,
        "3.0.1": True,
        "2.0.99": False,
    }
    for version, want_ok in cases.items():
        proc = subprocess.run(
            ["bash", "-c", expr],
            env={"MIN": floor, "V": version, "PATH": "/usr/bin:/bin:/usr/local/bin"},
            check=False,
        )
        got_ok = proc.returncode == 0
        assert got_ok is want_ok, f"{version} vs floor {floor}: got ok={got_ok}"


def test_launcher_prefers_the_pinned_build():
    script = device_launch.build_launch_script()
    assert device_launch.CLAUDE_PINNED_VERSION in script
    assert ".local/share/claude/versions/" in script
    # PATH stays as the last resort so a device that pins nothing still runs —
    # but it is still subject to the floor above.
    assert "command -v claude" in script


def test_base_args_keep_the_flags_and_drop_the_bare_binary():
    assert device_launch.CLAUDE_BASE_ARGS.startswith(" --dangerously-skip-permissions")
    assert "AskUserQuestion" in device_launch.CLAUDE_BASE_ARGS
    assert not device_launch.CLAUDE_BASE_ARGS.lstrip().startswith("claude")


def test_token_file_is_minted_once_and_reused(tmp_path):
    """An adopted `claude` keeps the token it booted with.

    So the launcher must MINT on first launch and REUSE afterwards. Running the
    real shell fragment twice is the only way to prove that: a Python
    reimplementation would test the reimplementation.
    """
    token_file = tmp_path / "rv.token"
    fragment = """
if [ ! -s "$T" ] || [ ! -O "$T" ]; then
  ( umask 077
    head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \\n' > "$T.tmp" ) \\
    && mv "$T.tmp" "$T" || rm -f "$T.tmp"
fi
cat "$T"
"""
    env = {"T": str(token_file), "PATH": "/usr/bin:/bin:/usr/local/bin"}
    first = subprocess.run(
        ["bash", "-c", fragment], env=env, capture_output=True, text=True, check=True
    ).stdout.strip()
    second = subprocess.run(
        ["bash", "-c", fragment], env=env, capture_output=True, text=True, check=True
    ).stdout.strip()

    assert len(first) == 48, f"expected 24 random bytes as hex, got {first!r}"
    assert first == second, "a second launch minted a new token — adopt would break"
    # 0600: the token is the only thing standing between a local process and the
    # ability to put words in the agent's mouth.
    assert oct(token_file.stat().st_mode)[-3:] == "600"


def test_launcher_fragment_matches_the_tested_one():
    """The fragment above is a copy; keep it honest.

    If the launcher's minting changes and this copy does not, the test above
    keeps passing while proving nothing about production.
    """
    script = device_launch.build_launch_script()
    for line in (
        'if [ ! -s "$CHEESE_RV_TOKEN_FILE" ] || [ ! -O "$CHEESE_RV_TOKEN_FILE" ]; then',
        "head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \\n'",
    ):
        assert line in script, f"launcher no longer contains: {line}"


def test_ci_e2e_pin_matches_the_launcher_pin():
    """CI installs a pinned Claude Code for the delivery e2e.

    Below the launcher's floor the rendezvous socket never binds, so the whole
    suite would exercise a transport production does not use — and would fail in
    a way that looks like a bug in the delivery code. Drifting these apart is a
    one-line edit in a file nobody reads while working on delivery, so it is a
    test rather than a comment.
    """
    workflow = (
        Path(__file__).resolve().parents[3] / ".github" / "workflows" / "cli.yml"
    ).read_text()
    assert (
        f"@anthropic-ai/claude-code@{device_launch.CLAUDE_PINNED_VERSION}" in workflow
    )


def test_sandbox_image_pin_matches_the_launcher_pin():
    """The sandbox image bakes the same Claude Code the launcher installs.

    A topic can run on either side — an agent container the backend spawns, or
    an enrolled device — and "the same turn" has to mean the same runtime. The
    image used to install whatever was newest at build time, so any rebuild
    could move it, silently and for an unrelated reason.
    """
    dockerfile = (
        Path(__file__).resolve().parents[2] / "sandbox" / "Dockerfile"
    ).read_text()
    assert (
        f"ARG CLAUDE_CODE_VERSION={device_launch.CLAUDE_PINNED_VERSION}" in dockerfile
    )
