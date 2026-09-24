"""Device launcher: the isolated config, the pinned build, and the runner it starts."""

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
from pathlib import Path

import pytest

from app.domain.agent import machine_launcher
from app.domain.agent.harness.claude_code import device_launch
from app.domain.agent.harness.claude_code.cli import DISALLOWED_TOOLS, LAUNCH_ARGS
from app.domain.agent.harness.claude_code.session_launch import (
    ClaudeLaunch,
    session_settings,
)
from app.domain.agent.harness.launch import MachinePlace

PIN = device_launch.CLAUDE_PINNED_VERSION
STATE = "$HOME/.cheese/harness/p/r/claude-code/deadbeef"


def _script(**named) -> str:
    named.setdefault("state", STATE)
    return device_launch.build_launch_script(**named)


def _tmux_ge_30() -> bool:
    if not shutil.which("tmux"):
        return False
    out = subprocess.run(["tmux", "-V"], capture_output=True, text=True).stdout
    m = re.search(r"(\d+)\.(\d+)", out)
    return bool(m) and (int(m.group(1)), int(m.group(2))) >= (3, 0)


def _clean_environ() -> dict[str, str]:
    """The test runner's environment, minus what would steer a launch.

    A developer running this inside tmux carries TMUX, and the launcher asks
    that server which session it is in.
    """
    env = dict(os.environ)
    for key in ("TMUX", "TMUX_PANE", "CLAUDE_CONFIG_DIR", "CHEESE_API"):
        env.pop(key, None)
    for key in list(env):
        if key.lower() in ("https_proxy", "http_proxy", "all_proxy"):
            env.pop(key)
    return env


def test_launch_timings_append_without_logging_credentials(tmp_path):
    script = _script()
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


def _place(
    *,
    home_dir="/dev/home",
    work_dir="/dev/work",
    topic_id="",
    execution_target=None,
    ca_pem="",
) -> MachinePlace:
    return MachinePlace(
        home=home_dir,
        workdir=work_dir,
        store="$HOME/.cheese/store/P",
        state=STATE,
        api_base="http://h",
        project_id="P",
        topic_id=topic_id,
        agent_handle="ops",
        execution_target=execution_target,
        ca_pem=ca_pem,
    )


def _screen_launch(
    *,
    token="tok",
    resume_session_id=None,
    extra_env=None,
    system_prompt="",
    **place,
) -> tuple[list[str], dict[str, str]]:
    """What the device channel does: say where, ask the plan what to run.

    A helper here rather than in the product, because assembling the two halves
    is the CHANNEL's job — this is how a test reaches the same result without
    standing up a device.
    """
    where = _place(**place)
    plan = ClaudeLaunch(
        system_prompt=system_prompt, resume_session_id=resume_session_id
    )
    command, env = machine_launcher.screen_launch(where, plan.on(where), token=token)
    env.update(extra_env or {})
    return command, env


def test_build_screen_launch_shapes_command_and_env():
    command, env = _screen_launch(
        token="scoped-tok", extra_env={"ANTHROPIC_BASE_URL": "http://gw"}
    )
    assert command[0] == "bash" and command[1] == "-lc"
    script = command[2]
    assert 'cat > "$CLAUDE_CONFIG_DIR/settings.json"' in script
    # What the screen runs is the runner; claude is the command it is handed.
    assert script.rstrip().endswith('exit "$RESULT"')
    assert 'eval "exec $ENVIRONMENT_CMD"$CLAUDE_RUNNER""' in script
    assert 'export CHEESE_CLAUDE_COMMAND="$CLAUDE"' in script
    assert env["CHEESE_TOKEN"] == "scoped-tok"
    assert env["CHEESE_HOME"] == "/dev/home" and env["CHEESE_WORK"] == "/dev/work"
    assert env["CHEESE_AUTHOR"] == "ops"
    # 结论 46: the binding is resolved at admission, never carried on the screen.
    assert "CLAUDE_MODEL" not in env
    assert env["ANTHROPIC_BASE_URL"] == "http://gw"
    assert "CHEESE_RESUME_SESSION" not in env


def test_a_resume_is_offered_to_the_runner_through_the_environment():
    _, env = _screen_launch(resume_session_id="11111111-2222-3333-4444-555555555555")
    assert env["CHEESE_RESUME_SESSION"] == "11111111-2222-3333-4444-555555555555"


def test_the_contract_is_the_argv_a_live_session_cannot_adopt():
    holes = device_launch.launch_holes(state=STATE)
    assert holes.command == '"$CLAUDE_RUNNER"'
    assert shlex.split(holes.contract) == LAUNCH_ARGS


def test_agent_authors_real_commit_and_platform_commits_it(tmp_path):
    _, env = _screen_launch(
        token="test", home_dir=str(tmp_path), work_dir=str(tmp_path)
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
    _, env = _screen_launch()
    # 深度的默认值。
    assert env["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"] == "1"
    # How many pieces of work a room runs at once, sharing one tree.
    assert env["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"] == "4"
    # The build's own feedback tool would send a report nowhere the platform reads.
    assert env["DISABLE_FEEDBACK_COMMAND"] == "1"


def test_how_deep_a_session_may_spawn_is_a_deployment_setting(monkeypatch):
    """深度是部署设的，不是写死的设计约束（结论 33）。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "claude_code_max_subagent_spawn_depth", 3)

    _, env = _screen_launch()

    assert env["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"] == "3"


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
    — so a node step aborts the whole launch with nothing saying why.
    """
    assert not any(c.startswith("node ") for c in _commands_in(_script()))


def test_the_proxy_ca_rides_the_script_and_names_its_real_path():
    """Subscription turns: the backend's CA path means nothing on the device, and
    the server cannot know the device user's home — so the CA BYTES travel in the
    script, and the script itself exports NODE_EXTRA_CA_CERTS at the real
    (post-substitution) location."""
    pem = "-----BEGIN CERTIFICATE-----\nDEVCA\n-----END CERTIFICATE-----"
    script = _script(ca_pem=pem)
    assert "cat > \"$HOME/.claude/proxy-ca.pem\" <<'CHEESECA'" in script
    assert "DEVCA" in script
    assert 'export NODE_EXTRA_CA_CERTS="$HOME/.claude/proxy-ca.pem"' in script


def test_no_ca_means_no_ca_block():
    """The gateway path must not write a stray cert file or export a CA path
    that points at nothing (an empty NODE_EXTRA_CA_CERTS breaks TLS wholesale)."""
    script = _script()
    assert "CHEESECA" not in script
    assert 'export NODE_EXTRA_CA_CERTS="$HOME/.claude/proxy-ca.pem"' not in script


def test_build_screen_launch_threads_the_ca_through():
    command, _env = _screen_launch(
        ca_pem="-----BEGIN CERTIFICATE-----\nDEVCA\n-----END CERTIFICATE-----"
    )
    assert "DEVCA" in command[2]


# --- the prepare hole, run for real -------------------------------------------
# What it decides — which binary, whether it is new enough, what argv the runner
# is handed — is only ever evaluated by a shell on the machine.


def _stand_in_claude(path: Path, *, version: str = PIN) -> Path:
    """A `claude` that answers `--version` and otherwise records how it was run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = "--version" ]; then echo "{version} (Claude Code)"; exit 0; fi\n'
        'out="$(dirname "$0")/ran"\n'
        'printf "%s\\n" "$@" > "$out.args"\n'
        'printf "%s" "$CLAUDE_CONFIG_DIR" > "$out.config"\n'
        'printf "%s" "${BUN_OPTIONS:-}" > "$out.bun"\n'
        'printf "%s" "${CLAUDE_CODE_OAUTH_TOKEN:-}" > "$out.token"\n'
        'printf "%s" "${CLAUDE_SECURESTORAGE_CONFIG_DIR:-}" > "$out.store"\n'
    )
    path.chmod(0o755)
    return path


def _bootstrap_passthrough(bindir: Path) -> None:
    """A `python3` whose executor-client `bootstrap` runs the command it wraps.

    Every launch hands `claude` to the executor client, which needs an executor
    to prepare against; this one passes the command straight through, so what the
    launcher decided is what the stand-in receives. Everything else reaches the
    real interpreter.
    """
    bindir.mkdir(parents=True, exist_ok=True)
    python = shutil.which("python3")
    client = bindir / "python3"
    client.write_text(
        "#!/bin/sh\n"
        'case "$1" in */remote-execution/client.py)\n'
        '  [ "$2" = bootstrap ] && shift 3 && exec "$@" ;;\n'
        "esac\n"
        f'exec {shlex.quote(python)} "$@"\n'
    )
    client.chmod(0o755)


def _run_prepare(tmp_path, *, api: str = "", system_prompt: str = "", curl=None):
    """The prepare hole alone, in `sh`, as the launcher reaches it: with the
    session home, the owner's home and the executor target already set."""
    owner = tmp_path / "owner"
    session = owner / ".cheese/home/room"
    session.mkdir(parents=True)
    bindir = tmp_path / "bin"
    _bootstrap_passthrough(bindir)
    if curl is not None:
        (bindir / "curl").write_text(curl)
        (bindir / "curl").chmod(0o755)
    holes = device_launch.launch_holes(state=STATE, system_prompt=system_prompt)
    if system_prompt:
        (session / ".claude").mkdir()
        (session / ".claude/cheese-system-prompt.md").write_text(system_prompt)
    script = (
        "set -e\ncheese_launch_phase() { :; }\n"
        + holes.prepare
        + 'printf "%s\\n" "$CHEESE_CLAUDE_COMMAND" > "$HOME/command"\n'
        + 'printf "%s\\n" "$CLAUDE_RUNNER" > "$HOME/runner"\n'
    )
    env = {
        **_clean_environ(),
        "PATH": f"{bindir}:/usr/bin:/bin",
        "HOME": str(session),
        "REAL_HOME": str(owner),
        "CHEESE_EXECUTION_TARGET": json.dumps({"kind": "deferred"}),
    }
    if api:
        env["CHEESE_API"] = api
    result = subprocess.run(
        ["sh", "-c", script], env=env, capture_output=True, text=True, timeout=30
    )
    return owner, session, result


def _argv(command: str, *, cwd: Path) -> list[str]:
    """What the runner's `sh -c "exec <command>"` would hand exec."""
    return subprocess.check_output(
        ["sh", "-c", 'eval "set -- $1"; printf "%s\\n" "$@"', "sh", command],
        text=True,
        cwd=cwd,
    ).splitlines()


def test_the_runner_is_handed_the_pinned_build_behind_the_executor_client(tmp_path):
    owner = tmp_path / "owner"
    claude = _stand_in_claude(owner / ".local/bin/claude")
    _, session, result = _run_prepare(tmp_path, system_prompt="be kind\n")

    assert result.returncode == 0, result.stderr
    argv = _argv((session / "command").read_text().strip(), cwd=tmp_path)
    assert argv[:4] == [
        "python3",
        f"{session}/.cheese/remote-execution/client.py",
        "bootstrap",
        f"{session}/.cheese/remote-target.json",
    ]
    assert argv[4:] == [
        str(claude),
        *LAUNCH_ARGS,
        "--append-system-prompt-file",
        f"{session}/.claude/cheese-system-prompt.md",
    ]
    # None of the flags the terminal launch needed survives the move to `-p`.
    assert "--dangerously-skip-permissions" not in argv
    assert "--remote-control" not in argv
    assert json.loads((session / ".cheese/remote-target.json").read_text()) == {
        "kind": "deferred"
    }


def test_an_empty_system_prompt_adds_no_flag(tmp_path):
    _stand_in_claude(tmp_path / "owner/.local/bin/claude")
    _, session, result = _run_prepare(tmp_path)

    assert result.returncode == 0, result.stderr
    command = (session / "command").read_text()
    assert "--append-system-prompt-file" not in command


def test_the_runner_keeps_its_state_in_the_owners_home(tmp_path):
    """The state is the CONNECTOR's `$HOME/...`: the owner's, which outlives the
    session home a relaunch rebuilds, and whose path the backend derives the
    socket from."""
    _stand_in_claude(tmp_path / "owner/.local/bin/claude")
    owner, session, result = _run_prepare(tmp_path)

    assert result.returncode == 0, result.stderr
    state = owner / STATE.removeprefix("$HOME/")
    assert state.is_dir()
    assert oct(state.stat().st_mode & 0o777) == "0o700"
    (artifact,) = state.glob("runner-*.pyz")
    assert artifact.read_bytes() == device_launch.build()
    line = (session / "runner").read_text().strip()
    assert _argv(line, cwd=tmp_path) == [
        "python3",
        "-I",
        "-S",
        str(artifact),
        "--state",
        str(state),
    ]
    # Its own stderr is kept where the pane's death cannot take it.
    assert line.endswith(f'2>>"{state}/runner.log"')


def test_a_live_runners_archive_is_never_overwritten(tmp_path):
    """The archive is named by its digest: a launch that ships the same runner
    writes nothing, and one that ships a new one leaves the old file in place
    for the runner still importing from it."""
    _stand_in_claude(tmp_path / "owner/.local/bin/claude")
    owner, _, first = _run_prepare(tmp_path)
    assert first.returncode == 0, first.stderr
    state = owner / STATE.removeprefix("$HOME/")
    (artifact,) = state.glob("runner-*.pyz")
    stamp = artifact.stat().st_mtime_ns
    live = state / "runner-0000.pyz"
    live.write_bytes(b"in use")

    shutil.rmtree(owner / ".cheese/home")
    _, _, second = _run_prepare(tmp_path)

    assert second.returncode == 0, second.stderr
    assert artifact.stat().st_mtime_ns == stamp
    assert live.read_bytes() == b"in use"


def test_a_build_under_the_floor_is_refused_by_name(tmp_path):
    _stand_in_claude(tmp_path / "owner/.local/bin/claude", version="2.1.100")
    _, session, result = _run_prepare(tmp_path)

    assert result.returncode == 1
    assert f"claude 2.1.100 at {tmp_path}/owner/.local/bin/claude is older" in (
        result.stderr
    )
    assert not (session / "command").exists()


def test_no_claude_anywhere_is_named_not_started(tmp_path):
    _, session, result = _run_prepare(tmp_path)

    assert result.returncode == 1
    assert "cheese-launch: no claude binary found" in result.stderr
    assert not (session / "command").exists()


def _fetching_curl(log: Path, version: str = PIN) -> str:
    """A `curl` that logs its URL and writes a stand-in claude to `-o`."""
    return (
        "#!/bin/sh\n"
        'dest=""; url=""\n'
        'while [ "$#" -gt 0 ]; do\n'
        '  case "$1" in -o) shift; dest="$1" ;; http*) url="$1" ;; esac\n'
        "  shift\n"
        "done\n"
        f'echo "$url" >> {shlex.quote(str(log))}\n'
        "cat > \"$dest\" <<'AGENT'\n#!/bin/sh\n"
        f'if [ "$1" = "--version" ]; then echo "{version} (Claude Code)"; fi\n'
        "AGENT\n"
    )


def test_a_missing_pin_is_fetched_from_the_platform_and_preferred(tmp_path):
    """A pin bump reaches a machine enrolled under the old pin at its next
    launch: the pinned build is placed from the platform and wins over whatever
    the machine has on its own."""
    _stand_in_claude(tmp_path / "owner/.local/bin/claude")
    log = tmp_path / "curl.log"
    owner, session, result = _run_prepare(
        tmp_path, api="https://platform.invalid/", curl=_fetching_curl(log)
    )

    assert result.returncode == 0, result.stderr
    pin = owner / ".cheese/claude/versions" / PIN
    assert pin.is_file() and os.access(pin, os.X_OK)
    (url,) = log.read_text().splitlines()
    assert re.fullmatch(
        rf"https://platform\.invalid/connector/claude/{re.escape(PIN)}/"
        r"(linux|darwin)-(x64|arm64)(-musl)?/claude",
        url,
    )
    argv = _argv((session / "command").read_text().strip(), cwd=tmp_path)
    assert argv[4] == str(pin)


def test_a_failed_fetch_falls_back_to_the_machines_own_build(tmp_path):
    claude = _stand_in_claude(tmp_path / "owner/.local/bin/claude")
    owner, session, result = _run_prepare(
        tmp_path, api="https://platform.invalid", curl="#!/bin/sh\nexit 22\n"
    )

    assert result.returncode == 0, result.stderr
    assert f"could not fetch claude {PIN}" in result.stderr
    assert not list((owner / ".cheese/claude/versions").iterdir())
    argv = _argv((session / "command").read_text().strip(), cwd=tmp_path)
    assert argv[4] == str(claude)


def test_a_present_pin_is_not_fetched_again(tmp_path):
    owner = tmp_path / "owner"
    pin = _stand_in_claude(owner / ".cheese/claude/versions" / PIN)
    log = tmp_path / "curl.log"
    _, session, result = _run_prepare(
        tmp_path, api="https://platform.invalid", curl=_fetching_curl(log)
    )

    assert result.returncode == 0, result.stderr
    assert not log.exists()
    argv = _argv((session / "command").read_text().strip(), cwd=tmp_path)
    assert argv[4] == str(pin)


# --- the whole launcher, run for real ------------------------------------------


def _machine(tmp_path, *, api: str = ""):
    """An owner's home with a stand-in claude, a project, and the launch env."""
    owner = tmp_path / "owner"
    (owner / ".claude").mkdir(parents=True)
    work = tmp_path / "project"
    work.mkdir()
    session = owner / ".cheese/home/room"
    bindir = tmp_path / "bin"
    _bootstrap_passthrough(bindir)
    claude = _stand_in_claude(owner / ".local/bin/claude")
    env = {
        **_clean_environ(),
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "HOME": str(owner),
        "CHEESE_HOME": str(session),
        "CHEESE_WORK": str(work),
        "CHEESE_TOPIC": "room",
        "CHEESE_AUTHOR": "ops",
        "CHEESE_EXECUTION_TARGET": json.dumps({"kind": "deferred"}),
    }
    if api:
        env["CHEESE_API"] = api
        # Keep the detached document-tool fetch from starting after the test.
        chain = owner / ".cheese/toolchain"
        for tool, version, kind, name in machine_launcher.toolchain.PLACEMENTS:
            target = chain / (
                "fonts/" + machine_launcher.toolchain.fonts_pin()
                if kind == "font"
                else f"{tool}/{version}"
            )
            target.mkdir(parents=True, exist_ok=True)
            (target / name).write_text("fixture")
            (target / name).chmod(0o755)
    return owner, session, work, claude, env


def _launch(tmp_path, env, **named):
    # Devices execute a shipped file; Linux rejects this script's size in argv.
    launcher = tmp_path / "launch.sh"
    launcher.write_text(_script(**named))
    return subprocess.run(
        ["sh", str(launcher)], env=env, capture_output=True, text=True, timeout=60
    )


def test_the_launch_starts_the_runner_and_the_runner_starts_claude(tmp_path):
    """The real archive, started the way the screen starts it, starting the
    stand-in with the argv the launcher decided and a session id of its own."""
    owner, session, _work, claude, env = _machine(tmp_path)
    env["CHEESE_RESUME_SESSION"] = "no-such-transcript"

    result = _launch(tmp_path, env, system_prompt="be kind")

    state = owner / STATE.removeprefix("$HOME/")
    assert result.returncode == 0, result.stderr + (
        (state / "runner.log").read_text() if (state / "runner.log").exists() else ""
    )
    args = (claude.parent / "ran.args").read_text().splitlines()
    assert args[: len(LAUNCH_ARGS)] == LAUNCH_ARGS
    assert args[len(LAUNCH_ARGS) : len(LAUNCH_ARGS) + 2] == [
        "--append-system-prompt-file",
        f"{session}/.claude/cheese-system-prompt.md",
    ]
    # The offered transcript is not on this disk, so the session starts afresh.
    assert args[-2] == "--session-id"
    uuid.UUID(args[-1])
    assert (claude.parent / "ran.config").read_text() == f"{session}/.claude"
    assert (session / ".claude/cheese-system-prompt.md").read_text() == "be kind\n"
    assert (state / "records.sqlite").is_file()


def test_full_launcher_installs_platform_cli_without_network(tmp_path):
    _owner, session, _work, claude, env = _machine(tmp_path)
    network = tmp_path / "network.calls"
    curl = tmp_path / "bin/curl"
    curl.write_text(f'#!/bin/sh\necho attempted >> "{network}"\nexit 1\n')
    curl.chmod(0o755)

    result = _launch(tmp_path, env)

    assert result.returncode == 0, result.stderr
    assert not network.exists(), "room startup must not fetch the platform CLI"
    transport = session / ".claude/webfetch_transport.cjs"
    assert (
        transport.read_bytes()
        == Path(device_launch.__file__).with_name("webfetch_transport.cjs").read_bytes()
    )
    assert (claude.parent / "ran.bun").read_text() == f'"--preload={transport}"'
    installed = session / ".cheese/cheese"
    source = Path(device_launch.__file__).resolve().parents[5] / "sandbox/cheese"
    assert installed.read_bytes() == source.read_bytes()
    result = subprocess.run(
        [str(installed), "--help"], env=env, capture_output=True, text=True, timeout=5
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


def test_the_settings_file_written_is_the_sessions_settings(tmp_path):
    _owner, session, _work, _claude, env = _machine(tmp_path)

    result = _launch(tmp_path, env)

    assert result.returncode == 0, result.stderr
    written = json.loads((session / ".claude/settings.json").read_text())
    assert written == session_settings()


def test_hosted_launch_preserves_owner_and_project_while_installing_skills(tmp_path):
    owner, session, work, _claude, env = _machine(
        tmp_path, api="https://fixture.invalid"
    )
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
    previous_chat_skill = session / ".claude/skills/cheese-chat/SKILL.md"
    previous_chat_skill.parent.mkdir(parents=True)
    previous_chat_skill.write_text("Previous generated chat guide\n")
    log = tmp_path / "curl.log"
    (tmp_path / "bin/curl").write_text(_fetching_curl(log))
    (tmp_path / "bin/curl").chmod(0o755)

    result = _launch(tmp_path, env)

    assert result.returncode == 0, result.stderr
    assert len(log.read_text().splitlines()) == 1
    assert {path: path.read_bytes() for path in protected} == before
    assert set(owner.iterdir()) == original_entries | {owner / ".cheese"}
    assert set(work.rglob("*")) == project_entries
    assert (owner / ".cheese/claude/versions" / PIN).is_file()
    for name in ("cheese-docs",):
        assert (session / ".claude/skills" / name / "SKILL.md").is_file()
    assert not previous_chat_skill.exists()


def test_the_session_the_runner_starts_logs_in_with_the_hosts_own_store(tmp_path):
    """The credentials step runs before the runner, and what it chose is what the
    session the runner starts carries: the host user's store, and no token of
    its own that would win over it."""
    owner, _session, _work, claude, env = _machine(tmp_path)
    (owner / ".claude/.credentials.json").write_text('{"claudeAiOauth": {}}')
    env["CLAUDE_CODE_OAUTH_TOKEN"] = "stale-inherited-token"

    result = _launch(tmp_path, env)

    assert result.returncode == 0, result.stderr
    assert (claude.parent / "ran.store").read_text() == f"{owner}/.claude"
    assert (claude.parent / "ran.token").read_text() == ""


# --- the tunnel branch ------------------------------------------------------
# A remote machine cannot dial the meter's listener on the ghg network, so its
# CONNECT rides a helper on its own loopback. The helper is the fragile part:
# started in the wrong process tree it dies with the connector while claude
# lives on, pointed at a dead port — every turn then fails looking exactly like
# a stalled model, which is the most expensive failure to diagnose.


def _launch_with_tunnel(**overrides):
    env = {
        "CHEESE_TUNNEL_URL": "wss://gw.example/api/llm/tunnel",
        "CLAUDE_CODE_OAUTH_TOKEN": "scoped.session.token",
    }
    env.update(overrides)
    command, _env = _screen_launch(token="scoped-tok", extra_env=env)
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


def _run_tunnel_prefix(tmp_path, up_script: str):
    """The launcher's tunnel lines, run for real in front of a stand-in agent
    that reports the proxy it was started with."""
    script = _launch_with_tunnel()
    # Not via bash's /dev/tcp anywhere: this runs under `sh`, which is dash on
    # the machine images, where that redirect fails on every attempt.
    assert "/dev/tcp/" not in script
    start = script.rindex(
        'if [ -n "${CHEESE_TUNNEL_URL:-}" ]; then',
        0,
        script.index('TUNNEL_PORT="$(sh "$HOME/.cheese/cheese-tunnel-up")"'),
    )
    end = script.index("\nfi\n", start) + len("\nfi\n")
    (tmp_path / ".cheese").mkdir(exist_ok=True)
    (tmp_path / ".cheese" / "cheese-tunnel-up").write_text(up_script)
    env = {
        key: value
        for key, value in os.environ.items()
        if key.lower() not in ("https_proxy", "http_proxy", "all_proxy")
    }
    return subprocess.run(
        ["sh", "-c", script[start:end] + 'printf "agent:%s" "$HTTPS_PROXY"\n'],
        env={**env, "HOME": str(tmp_path), "CHEESE_TUNNEL_URL": "wss://x/y"},
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_the_agent_is_started_on_the_port_its_helper_bound(tmp_path):
    """The helper's port is the machine's choice, reported by the script that
    brought it up. `claude` reads HTTPS_PROXY once, at startup, so the port has
    to be in its environment before it starts — any other port is either dead or
    somebody else's helper, carrying somebody else's credential."""
    result = _run_tunnel_prefix(tmp_path, "#!/bin/sh\necho 40123\n")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "agent:http://127.0.0.1:40123"


def test_no_agent_starts_when_its_helper_is_not_ready(tmp_path):
    """A `claude` started without its helper fails every turn looking like a
    stalled model. The launch has to stop here, visibly, instead."""
    result = _run_tunnel_prefix(tmp_path, "#!/bin/sh\necho 40123\nexit 1\n")

    assert result.returncode == 1
    assert "agent:" not in result.stdout


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


def _tunnel_up_home(tmp_path, name: str = "home"):
    """A room HOME laid out the way the launcher leaves one, with the real helper
    and the real up-script. The helper only dials its URL when a client connects,
    so an unreachable one is enough for everything this script decides."""
    from app.domain.agent import machine_tunnel
    from app.domain.agent.machine_launcher import CHEESE_TUNNEL_UP

    home = tmp_path / name
    for directory in (".claude", ".cheese"):
        (home / directory).mkdir(parents=True, exist_ok=True)
    (home / ".cheese" / "cheese-tunnel.py").write_text(
        Path(machine_tunnel.__file__).read_text()
    )
    (home / ".cheese" / "cheese-tunnel.token").write_text("scoped\n")
    up = home / ".cheese" / "cheese-tunnel-up"
    up.write_text(CHEESE_TUNNEL_UP)
    up.chmod(0o755)
    return home, up


def _listening(port: int) -> bool:

    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
    except OSError:
        return False
    return True


def _await(condition, *, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


def _helper_pid(home) -> int:
    return int((home / ".cheese" / "cheese-tunnel.pid").read_text().strip())


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _kill_helper(home) -> None:
    import signal

    try:
        pid = _helper_pid(home)
    except (OSError, ValueError):
        return
    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGKILL)
    _await(lambda: not _pid_alive(pid), timeout=5)


def _run_tunnel_up(home):
    return subprocess.run(
        ["sh", str(home / ".cheese" / "cheese-tunnel-up")],
        env={
            **os.environ,
            "HOME": str(home),
            "CHEESE_TUNNEL_URL": "ws://127.0.0.1:9/llm/tunnel",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )


def _up(home) -> int:
    """Run the up-script, and return the port it printed — which must be all it
    printed, since the launcher exports stdout verbatim as the proxy's port."""
    result = _run_tunnel_up(home)
    assert result.returncode == 0, result.stderr
    assert re.fullmatch(r"[0-9]+\n", result.stdout), result.stdout
    port = int(result.stdout)
    assert (home / ".cheese" / "cheese-tunnel.port").read_text().strip() == str(port)
    return port


def _stamp_of(home) -> str:
    return subprocess.run(
        ["cksum", str(home / ".cheese" / "cheese-tunnel.py")],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()[0]


@pytest.mark.skipif(not _tmux_ge_30(), reason="needs a real tmux >= 3.0")
def test_the_tunnel_helper_outlives_the_window_that_started_it(tmp_path):
    """A caller can run this script as the command of its own tmux window, and
    that window is torn down the instant the command returns. A helper that
    dies with it leaves `claude` — which read HTTPS_PROXY once at startup and
    cannot be told a new one — dialling a dead port for the life of the screen.

    Measured 2026-08-30: fifteen topics in exactly that state, their
    `cheese-tunnel.log` showing `tunnel listening` at the last launch's
    timestamp, one of them re-@'d four times in three hours without a single
    reply."""

    home, _up_script = _tunnel_up_home(tmp_path)
    port_file = home / ".cheese" / "cheese-tunnel.port"
    tmux = shutil.which("tmux") or "tmux"  # the skipif above already found it
    sock = f"/tmp/cu{os.getpid()}.sock"  # noqa: S108 — ephemeral, killed below
    try:
        subprocess.run(
            [tmux, "-S", sock, "new-session", "-d", "-s", "s", "sleep 60"], check=True
        )
        subprocess.run(
            [
                tmux, "-S", sock, "new-window", "-d", "-t", "s:", "-n", "cheese-tunnel",
                f"HOME={home} CHEESE_TUNNEL_URL=ws://127.0.0.1:9/x "
                f'exec sh "{home}/.cheese/cheese-tunnel-up"',
            ],
            check=True,
        )  # fmt: skip
        assert _await(port_file.exists), "the helper never reported a port"
        port = int(port_file.read_text())
        assert _await(lambda: _listening(port)), (
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
        _kill_helper(home)
        subprocess.run([tmux, "-S", sock, "kill-server"], capture_output=True)


def test_rooms_launched_together_each_get_their_own_helper(tmp_path):
    """~50 rooms share one host, and a port chosen by anything but the machine
    lands two of them on one number. When that happened the second helper died,
    the first answered the readiness check in its place, and the second room's
    `claude` ran every request on the first room's credential. Each room must
    get a port that its OWN helper holds, even when they all start at once."""
    homes = [_tunnel_up_home(tmp_path, f"room-{index}")[0] for index in range(6)]
    results: dict[int, subprocess.CompletedProcess] = {}

    def launch(index: int) -> None:
        results[index] = _run_tunnel_up(homes[index])

    threads = [threading.Thread(target=launch, args=(i,)) for i in range(len(homes))]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        ports = []
        for index in range(len(homes)):
            result = results[index]
            assert result.returncode == 0, result.stderr
            ports.append(int(result.stdout))
            assert _listening(ports[-1])
        assert len(set(ports)) == len(homes), f"rooms share a helper port: {ports}"

        # And each port is held by that room's helper: retiring one room's helper
        # takes down its port and nobody else's.
        _kill_helper(homes[0])
        assert _await(lambda: not _listening(ports[0]))
        assert all(_listening(port) for port in ports[1:])
    finally:
        for home in homes:
            _kill_helper(home)


def test_a_foreign_listener_on_the_recorded_port_is_never_adopted(tmp_path):
    """The recorded port is only a preference. While this room's helper was gone,
    anything — another room's helper among them — may have bound it, and a
    readiness check that asks only "does something answer there" hands this
    room's `claude` to that listener. The script must start a helper of its own
    somewhere else and report THAT port."""

    home, _up_script = _tunnel_up_home(tmp_path)
    cheese = home / ".cheese"
    with socket.socket() as foreign:
        foreign.bind(("127.0.0.1", 0))
        foreign.listen(8)
        taken = foreign.getsockname()[1]
        # What a room is left with after its helper died: its port, its stamp,
        # and a pid that no longer resolves.
        dead = subprocess.Popen(["true"])
        dead.wait()
        (cheese / "cheese-tunnel.pid").write_text(f"{dead.pid}\n")
        (cheese / "cheese-tunnel.stamp").write_text(f"{_stamp_of(home)}\n")
        (cheese / "cheese-tunnel.port").write_text(f"{taken}\n")
        try:
            port = _up(home)

            assert port != taken, "reported ready on somebody else's listener"
            assert _listening(port)
            # It is ours: our helper going away takes that port with it, and the
            # foreign listener is untouched.
            _kill_helper(home)
            assert _await(lambda: not _listening(port))
            assert _listening(taken)
        finally:
            _kill_helper(home)


def test_a_live_pid_that_does_not_hold_the_recorded_port_is_not_adopted(tmp_path):
    """After a reboot the recorded pid can name some unrelated process, and the
    recorded port can be held by another room's helper. A live pid, a matching
    stamp and an answering port are all true then, and none of them makes that
    listener this room's. Adoption asks whether the pid holds the port."""

    home, _up_script = _tunnel_up_home(tmp_path)
    cheese = home / ".cheese"
    unrelated = subprocess.Popen(["sleep", "30"])
    try:
        with socket.socket() as foreign:
            foreign.bind(("127.0.0.1", 0))
            foreign.listen(8)
            taken = foreign.getsockname()[1]
            (cheese / "cheese-tunnel.pid").write_text(f"{unrelated.pid}\n")
            (cheese / "cheese-tunnel.stamp").write_text(f"{_stamp_of(home)}\n")
            (cheese / "cheese-tunnel.port").write_text(f"{taken}\n")

            port = _up(home)

            assert port != taken, "adopted a listener the recorded pid does not hold"
            assert _helper_pid(home) != unrelated.pid
            assert _listening(port)
            assert _listening(taken)
    finally:
        _kill_helper(home)
        unrelated.kill()
        unrelated.wait()


def test_a_helper_it_already_started_is_adopted_on_its_port(tmp_path):
    """Runs before every agent start or claim. Restarting a working helper each
    time would reset every in-flight connection, and would move the port out from
    under anything already dialling it."""
    home, _up_script = _tunnel_up_home(tmp_path)
    try:
        port = _up(home)
        first = _helper_pid(home)

        assert _up(home) == port
        assert _helper_pid(home) == first, "a healthy helper was restarted"
        assert _listening(port)
    finally:
        _kill_helper(home)


def test_a_helper_that_died_comes_back_on_the_port_it_had(tmp_path):
    """A helper that exits is started again on the port it recorded when that port
    is still free, so the address a room uses changes only when something else
    has taken it."""
    home, _up_script = _tunnel_up_home(tmp_path)
    try:
        port = _up(home)
        first = _helper_pid(home)
        _kill_helper(home)
        assert _await(lambda: not _listening(port))

        assert _up(home) == port
        assert _helper_pid(home) != first
        assert _listening(port)
    finally:
        _kill_helper(home)


def test_a_recorded_pid_that_is_alive_but_serves_no_port_is_replaced(tmp_path):
    """The recorded pid being alive proves only that SOME process holds that
    number — after a reboot, or on a box that has burnt through the pid space,
    that is a coincidence. Adopting on it would leave the port dead for the life
    of the screen, which is the failure this script exists to end."""
    home, _up_script = _tunnel_up_home(tmp_path)
    cheese = home / ".cheese"
    impostor = subprocess.Popen(["sh", "-c", "sleep 300"])
    try:
        port = _up(home)
        _kill_helper(home)
        # The state the box is actually found in: a pid that resolves, a stamp
        # that matches the helper on disk, a port file, and nothing listening.
        (cheese / "cheese-tunnel.pid").write_text(f"{impostor.pid}\n")
        assert not _listening(port)

        replaced = _up(home)

        assert _listening(replaced), "an impostor pid was adopted as a live helper"
        assert _helper_pid(home) != impostor.pid
    finally:
        _kill_helper(home)
        impostor.terminate()
        impostor.wait(timeout=5)


def test_a_helper_that_cannot_start_fails_the_launch_loudly(tmp_path):
    """Nothing about a `claude` pointed at no helper looks like a tunnel problem
    from outside — its turns just never answer. So a helper that exits instead of
    binding must fail this script, print no port, and say where to look."""
    home, _up_script = _tunnel_up_home(tmp_path)
    (home / ".cheese" / "cheese-tunnel.py").write_text(
        "import sys\nprint('cannot bind', file=sys.stderr)\nraise SystemExit(1)\n"
    )
    (home / ".cheese" / "cheese-tunnel.port").write_text("40999\n")

    started = time.monotonic()
    result = _run_tunnel_up(home)

    assert result.returncode != 0
    assert result.stdout == ""
    assert "cannot bind" in result.stderr
    # An exit is noticed when it happens, not at the end of the wait.
    assert time.monotonic() - started < 5


def test_the_helper_reports_only_a_port_it_actually_bound(tmp_path):
    """The port file is the helper's proof that it holds the port. Written after
    the bind it is exactly that; a failed bind must leave none behind, or a
    reader would take the foreign listener on that port for this helper."""

    from app.domain.agent import machine_tunnel

    def helper(port: int, port_file):
        return subprocess.Popen(
            [
                sys.executable, machine_tunnel.__file__,
                "--port", str(port), "--port-file", str(port_file),
                "--url", "ws://127.0.0.1:9/x", "--token", "scoped",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )  # fmt: skip

    with socket.socket() as foreign:
        foreign.bind(("127.0.0.1", 0))
        foreign.listen(1)
        taken = foreign.getsockname()[1]
        refused = helper(taken, tmp_path / "refused.port")
        assert refused.wait(timeout=10) != 0
        assert not (tmp_path / "refused.port").exists()

    chosen = tmp_path / "chosen.port"
    process = helper(0, chosen)
    try:
        assert _await(chosen.exists), "the helper never reported its port"
        assert _listening(int(chosen.read_text()))
    finally:
        process.kill()
        process.wait(timeout=5)


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


def _run_credentials_step(real_home, *, inherited_token: str | None = None) -> dict:
    """Run the shipped credentials step under sh; return the env it leaves."""
    script = _script()
    start = script.index("# The session's Claude login is its host's own.")
    end = script.index("cheese_launch_phase credentials_selected")
    step = script[start:end]
    env = {"PATH": "/usr/bin:/bin", "REAL_HOME": str(real_home)}
    if inherited_token is not None:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = inherited_token
    result = subprocess.run(
        ["sh", "-c", step + "\nenv"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )


def test_a_session_shares_the_host_users_own_claude_login(tmp_path):
    """One store for every session on the host: Claude Code's own refresh,
    under its own lock, then renews the login for all of them. An inherited env
    token would win over that store and never refresh, so it is not let through."""
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / ".credentials.json").write_text('{"claudeAiOauth": {}}')

    env = _run_credentials_step(tmp_path, inherited_token="stale-inherited-token")

    assert env["CLAUDE_SECURESTORAGE_CONFIG_DIR"] == f"{tmp_path}/.claude"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env


def test_a_host_with_no_claude_login_still_boots_on_the_meters_placeholder(tmp_path):
    """A deployment without a Claude login still runs projects on the API-key
    pool, so Claude Code has to boot; the value it boots on is the one the
    metering proxy refuses for a subscription turn."""
    import importlib.util

    core_path = (
        Path(__file__).resolve().parents[3]
        / "deploy"
        / "metering-proxy"
        / "cheese_billing_core.py"
    )
    spec = importlib.util.spec_from_file_location("cheese_billing_core", core_path)
    assert spec and spec.loader
    core = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(core)

    env = _run_credentials_step(tmp_path, inherited_token="stale-inherited-token")

    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == core.NO_LOGIN_PLACEHOLDER
    assert "CLAUDE_SECURESTORAGE_CONFIG_DIR" not in env


def test_a_host_with_a_setup_token_logs_its_sessions_in_with_it(tmp_path):
    (tmp_path / ".cheese").mkdir()
    (tmp_path / ".cheese" / "claude-setup-token").write_text("sk-ant-oat01-SETUP\n")

    env = _run_credentials_step(tmp_path, inherited_token="stale-inherited-token")

    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-SETUP"
    assert "CLAUDE_SECURESTORAGE_CONFIG_DIR" not in env


def test_the_tunnel_password_stays_the_scoped_token():
    """The helper's token proves which project may open a tunnel — the scoped
    cheese token's job. A Claude login in CLAUDE_CODE_OAUTH_TOKEN is a different
    credential, and stamping it as the CONNECT password would 407 every tunnel."""
    script = _script()
    start = script.index("<<TUNNELTOK\n") + len("<<TUNNELTOK\n")
    written = script[start : script.index("\nTUNNELTOK", start)]

    for connect in ["place-rc-token", ""]:
        result = subprocess.run(
            ["/bin/bash", "-c", "cat <<EOF\n" + written + "\nEOF"],
            env={
                "CHEESE_CONNECT_TOKEN": connect,
                "CHEESE_TOKEN": "hook-token",
                "CLAUDE_CODE_OAUTH_TOKEN": "host-setup-token",
            },
            check=True,
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == connect


def test_the_launcher_exports_the_config_dir():
    """CLAUDE_CONFIG_DIR is the isolation boundary itself, exported before the
    runner starts so the session it launches inherits it."""
    assert 'export CLAUDE_CONFIG_DIR="$HOME/.claude"' in _script()


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


# --- the settings a session starts with ----------------------------------------


def test_nothing_in_the_settings_observes_the_session():
    """The runner reads what the session does from its stdout. A hook left here
    to report it would be a second, racing account of the same turn."""
    hooks = session_settings()["hooks"]
    assert set(hooks) == {"SessionStart", "UserPromptSubmit"}
    for entries in hooks.values():
        (entry,) = entries
        assert [hook["command"] for hook in entry["hooks"]] == [
            "cheese sync-agents || true"
        ]


def test_the_settings_refuse_what_the_argv_refuses():
    settings = session_settings()
    assert settings["permissions"]["deny"] == DISALLOWED_TOOLS
    assert LAUNCH_ARGS[LAUNCH_ARGS.index("--disallowedTools") + 1 :] == DISALLOWED_TOOLS
    assert "AskUserQuestion" in DISALLOWED_TOOLS
    assert settings["attribution"] == {"sessionUrl": False}
    assert settings["enableArtifact"] is False


def test_no_mcp_server_is_planted_in_a_sandbox():
    """The platform plants NO MCP server.

    An earlier revision planted `mcp-server-fetch` to get a fetch path a
    deadline could reach. Measured against the same pages, that server extracts
    badly where it matters and returns the raw page instead of an answer, and
    Claude Code's own tool description tells the model to PREFER an MCP fetch
    tool whenever one exists — so planting one replaces the better default.
    """
    assert "mcpServers" not in session_settings()
    assert "mcp-server-fetch" not in _script()
    assert "--mcp-config" not in LAUNCH_ARGS


def test_a_summarisation_stream_that_stalls_is_bounded():
    """Keep the model-stream watchdog enabled alongside page-fetch handling."""
    assert session_settings()["env"]["CLAUDE_ENABLE_STREAM_WATCHDOG"]


def test_the_stream_watchdog_cannot_cut_a_turn_before_the_platform_does():
    """The watchdog's idle window must not be the shortest one on the path.

    Left to itself the CLI uses 180 s whenever it believes it is on the
    first-party API — which this deployment is, since `ANTHROPIC_BASE_URL` is
    deliberately unset — and no hop writes a keepalive byte, so a thinking
    window looks exactly like a dead connection and the turn is killed
    mid-stream.
    """
    from app.core.config import settings

    env = session_settings()["env"]
    assert int(env["CLAUDE_STREAM_IDLE_TIMEOUT_MS"]) > 180_000, (
        "still on the CLI's firstParty default"
    )
    assert (
        int(env["CLAUDE_STREAM_IDLE_TIMEOUT_MS"])
        > settings.agent_first_output_timeout_s * 1000
    )


def test_repaired_webfetch_is_offered_with_its_transport():
    """The native tool stays available; the launch preloads its transport (the
    preload reaching claude is checked by the full launch above)."""
    assert "WebFetch" not in DISALLOWED_TOOLS
    assert "WebFetch" not in session_settings()["permissions"]["deny"]
    assert 'cat > "$CLAUDE_CONFIG_DIR/webfetch_transport.cjs"' in _script()


# --- the supervisor: the shell the screen runs, around the runner --------------


def _supervised_program(tmp_path):
    agent = tmp_path / "agent.py"
    agent.write_text(
        "import os, pathlib, sys, time\n"
        f"pathlib.Path({str(tmp_path / 'agent.pid')!r}).write_text(str(os.getpid()))\n"
        "if os.environ.get('WAIT_FOR_INPUT'):\n"
        "    print(sys.stdin.readline().strip(), flush=True)\n"
        "    sys.exit(7)\n"
        "time.sleep(60)\n"
    )
    marker = "# The connector owns the terminal session."
    body = _script().split(marker, 1)[1]
    script = tmp_path / "supervise.sh"
    script.write_text("set -e\n" + marker + body)
    home = tmp_path / "home"
    (home / ".cheese").mkdir(parents=True)
    env = {
        **_clean_environ(),
        "HOME": str(home),
        "CLAUDE_RUNNER": f"{shlex.quote(sys.executable)} {shlex.quote(str(agent))}",
        "CHEESE_TUNNEL_URL": "",
        "CHEESE_PREVIEW_URL": "",
    }
    return script, env, tmp_path / "agent.pid"


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
def test_supervisor_preserves_input_and_exit_status(tmp_path, shell):
    """The runner holds claude's stdin for the session's life, so the screen's
    input has to reach it through the backgrounded `exec`, and its exit status
    is the screen's."""
    if shutil.which(shell) is None:
        pytest.skip(f"needs {shell}")
    script, env, agent_file = _supervised_program(tmp_path)
    proc = subprocess.Popen(
        [shell, str(script)],
        env={**env, "WAIT_FOR_INPUT": "1"},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        agent = _wait_file(agent_file)
        out, _ = proc.communicate("hello\n", timeout=5)
        assert out.strip() == "hello"
        assert proc.returncode == 7
        _assert_exited(agent)
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)


def test_closing_real_tmux_ends_the_runner(tmp_path):
    tmux = shutil.which("tmux")
    if tmux is None:
        pytest.skip("needs tmux")
    script, env, agent_file = _supervised_program(tmp_path)
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
            assert (
                run("list-sessions", "-F", "#{session_name}").stdout.strip() == "screen"
            )
            assert run("kill-session", "-t", "=screen").returncode == 0
            _assert_exited(agent)
        finally:
            run("kill-server")


@pytest.mark.skipif(not _tmux_ge_30(), reason="needs a real tmux >= 3.0")
def test_environment_prepares_tools_without_task_code_on_attach_and_reset(tmp_path):
    from app.domain.agent import environment_runner
    from app.domain.project.environment import EnvironmentConfig

    socket_path = f"/tmp/ce{os.getpid()}.sock"  # noqa: S108 — test-owned tmux socket
    home = tmp_path / "home"
    helpers = home / ".cheese"
    helpers.mkdir(parents=True)
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    shutil.copy(environment_runner.__file__, helpers / "cheese-environment.py")
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
        **_clean_environ(),
        "HOME": str(home),
        "CHEESE_WORK": str(work),
        "TMUX": f"{socket_path},1,0",
        "CLAUDE_RUNNER": f'"{agent}"',
        "CHEESE_ENVIRONMENT": json.dumps(config.snapshot()),
    }
    marker = "# The connector owns the terminal session."
    body = "set -e\n" + marker + _script().split(marker, 1)[1]

    def attach():
        # The supervisor carries the environment wrapper itself, so the block
        # below is the whole of what a launch runs after the harness's own half.
        tmux = shutil.which("tmux")
        if (
            subprocess.run(
                [tmux, "-S", socket_path, "has-session", "-t", "=screen"],
                capture_output=True,
            ).returncode
            == 0
        ):
            return
        keys = ("PATH", "HOME", "CHEESE_WORK", "CLAUDE_RUNNER", "CHEESE_ENVIRONMENT")
        command = shlex.join(
            [
                "env",
                *[f"{k}={env[k]}" for k in keys],
                "sh",
                "-c",
                'cd "$CHEESE_WORK"; ' + body,
            ]
        )
        subprocess.run(
            [tmux, "-S", socket_path, "new-session", "-d", "-s", "screen", command],
            check=True,
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
        subprocess.run(["tmux", "-S", socket_path, "kill-server"], capture_output=True)


def test_the_executor_client_is_told_the_config_dir_before_it_needs_it():
    """The client hands `claude` its config directory, and reads that directory
    from the environment rather than deriving it from where it was installed —
    only the launcher knows both. A launcher that invoked the client before
    exporting it would fail every executor-backed screen at startup, and nothing
    else in the script would say why."""
    script = _script(system_prompt="x")

    assert script.index('export CLAUDE_CONFIG_DIR="$HOME/.claude"') < script.index(
        'CLAUDE="python3 \\"$EXECUTOR_CLIENT\\" bootstrap'
    )
