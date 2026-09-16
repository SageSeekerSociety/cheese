"""平台那半边的启动脚本，用一个不是 Claude Code 的 harness 驱动。

The point of ``machine_launcher`` is that a second harness can start on a device
by filling five holes instead of copying a 559-line script. So these tests fill
them with something emphatically not Claude Code — a shell script that writes a
file — and assert the platform still did its whole job around it.

Whatever these tests prove about the platform half, they prove for every
harness. What a harness puts IN the holes is that harness's own test.
"""

import json
import os
import re
import subprocess
import time

import pytest

from app.domain.agent import machine_launcher
from app.domain.agent.harness.claude_code.session_launch import ClaudeLaunch
from app.domain.agent.harness.launch import MachinePlace
from app.domain.agent.harness.pi.device_launch import PiLaunch
from app.domain.project.environment import EnvironmentConfig


def _machine(tmp_path):
    """A device's home and workdir, plus the environment a screen carries."""
    home = tmp_path / "home"
    (home / ".cheese").mkdir(parents=True)
    work = home / "work"
    work.mkdir()
    return (
        home,
        work,
        {
            **os.environ,
            "HOME": str(home),
            "CHEESE_HOME": str(home),
            "CHEESE_WORK": str(work),
            "CHEESE_TOPIC": "11111111-1111-1111-1111-111111111111",
            "CHEESE_PROJECT": "22222222-2222-2222-2222-222222222222",
            "CHEESE_HOOK_URL": "http://127.0.0.1:1/hooks",
            "CHEESE_TOKEN": "scoped-token",
            "CHEESE_TOKEN_EXPIRES": str(int(time.time()) + 3600),
        },
    )


def _harness(tmp_path, script: str) -> str:
    """A harness's `prepare` hole: leave the command to run in ``$AGENT``.

    That indirection is the contract — ``command`` is substituted into the
    supervisor's ``eval``, so it is a shell WORD, and everything about how the
    agent was found stays on this side of the seam.
    """
    agent = tmp_path / "agent.sh"
    agent.write_text("#!/bin/sh\n" + script)
    agent.chmod(0o755)
    return f'AGENT="{agent}"\n'


def _run(tmp_path, env, **holes):
    """Write the launcher to a file and run it, the way a device does."""
    launcher = tmp_path / "launch.sh"
    launcher.write_text(machine_launcher.launch_script(**holes))
    return subprocess.run(
        ["sh", str(launcher)], env=env, capture_output=True, text=True, timeout=30
    )


def test_a_harness_that_is_only_a_command_still_gets_the_whole_platform(tmp_path):
    home, work, env = _machine(tmp_path)
    proof = tmp_path / "agent.ran"
    result = _run(
        tmp_path,
        env,
        prepare=_harness(tmp_path, f'pwd > "{proof}"\n'),
        contract="--pretend-flags",
        command="$AGENT",
    )

    assert result.returncode == 0, result.stderr
    # The agent ran, in the session's workdir.
    assert proof.read_text().strip() == str(work.resolve())
    # And the platform put its own half on the machine around it.
    for name in ("cheese", "cheese-hook", "cheese-drain", "cheese-environment.py"):
        assert (home / ".cheese" / name).is_file(), name
    assert (home / ".cheese/launch-contract").read_text() == "--pretend-flags"
    drain = dict(
        line.split("=", 1)
        for line in (home / ".cheese/cheese-drain.env").read_text().splitlines()
    )
    assert drain["CHEESE_TOKEN"] == '"scoped-token"'


def test_the_platform_cli_is_on_path_for_whatever_runs(tmp_path):
    home, _work, env = _machine(tmp_path)
    seen = tmp_path / "which.out"
    result = _run(
        tmp_path,
        env,
        prepare=_harness(
            tmp_path,
            f'command -v cheese > "{seen}"\ncommand -v cheese-hook >> "{seen}"\n',
        ),
        command="$AGENT",
    )

    assert result.returncode == 0, result.stderr
    assert seen.read_text().split() == [
        str(home / ".cheese/cheese"),
        str(home / ".cheese/cheese-hook"),
    ]


def test_the_agents_exit_status_is_the_launchers(tmp_path):
    _home, _work, env = _machine(tmp_path)
    result = _run(
        tmp_path,
        env,
        prepare=_harness(tmp_path, "exit 17\n"),
        command="$AGENT",
    )
    assert result.returncode == 17, result.stderr


def test_the_environment_runner_wraps_whichever_harness_was_asked_for(tmp_path):
    """``CHEESE_ENVIRONMENT`` is the platform's promise about the machine, so it
    holds the agent the room asked for — not one particular binary.

    The agent must start AFTER preparation and inside it: an agent exec'd
    beside the runner would find a machine the project has not been installed
    on yet, and nothing downstream would say why."""
    home, work, env = _machine(tmp_path)
    env["CHEESE_ENVIRONMENT"] = json.dumps(
        EnvironmentConfig(setup_script="echo setup >> setup").snapshot()
    )
    result = _run(
        tmp_path,
        env,
        prepare=_harness(tmp_path, "echo agent >> agents\n"),
        command="$AGENT",
    )

    assert result.returncode == 0, result.stderr
    assert (work / "setup").read_text() == "setup\n"
    assert (work / "agents").read_text() == "agent\n"
    status = json.loads((home / ".cheese-environment/status.json").read_text())
    assert status["state"] == "ready"


def test_each_hole_runs_where_the_platform_says_it_does(tmp_path):
    """The holes are a contract about ORDER — the one thing a harness cannot
    arrange for itself."""
    _home, work, env = _machine(tmp_path)
    order = tmp_path / "order"
    result = _run(
        tmp_path,
        env,
        staging=f'printf "staging " >> "{order}"\n',
        # The session's home is exported by now, so a harness writes its files
        # under it without knowing where the machine put it.
        configure=f'printf "configure:$HOME " >> "{order}"\n',
        # The platform CLI is on PATH by now, so this hole can report itself.
        credentials=f'printf "credentials:$(command -v cheese-hook) " >> "{order}"\n',
        # And by `prepare` the cwd is the workdir the agent will run in.
        prepare=f'printf "prepare:$(pwd) " >> "{order}"\n'
        + _harness(tmp_path, "true\n"),
        command="$AGENT",
    )

    assert result.returncode == 0, result.stderr
    stages = order.read_text().split()
    assert [stage.split(":")[0] for stage in stages] == [
        "staging",
        "configure",
        "credentials",
        "prepare",
    ]
    assert stages[1] == f"configure:{os.path.realpath(env['CHEESE_HOME'])}"
    assert stages[2].endswith("/.cheese/cheese-hook")
    assert stages[3] == f"prepare:{work.resolve()}"


def _skeleton() -> str:
    """The launcher's own shell, without the files it merely carries.

    A heredoc body is a payload — the platform CLI, the drainer, the tunnel
    helper — and what those say about any harness is their own business. What
    this module must not know is in the lines around them.
    """
    script = machine_launcher.launch_script(contract="", command="$AGENT")
    lines, delimiter = [], None
    for line in script.split("\n"):
        if delimiter is not None:
            if line == delimiter:
                delimiter = None
            continue
        lines.append(line)
        opened = re.search(r"<<'?([A-Z_]+)'?$", line)
        if opened:
            delimiter = opened.group(1)
    return "\n".join(lines)


@pytest.mark.parametrize(
    "harness_only",
    [
        "CLAUDE_BIN",
        "CLAUDE_CONFIG_DIR",
        "CLAUDE_BG_RENDEZVOUS_SOCK",
        "--append-system-prompt-file",
        "--resume",
        "--dangerously-skip-permissions",
        "cheese-system-prompt.md",
        "settings.json",
        "remote-execution",
        "warm-native-runner",
    ],
)
def test_the_platform_half_names_no_harness(harness_only):
    """什么进洞里、什么留在骨架上，靠的是这条。

    A skeleton that starts out knowing one harness's flags is a skeleton the
    second harness has to fork. The platform's own files live in
    ``$HOME/.cheese`` for the same reason: ``$HOME/.claude`` was one harness's
    directory that the platform had moved into, and the next reader could only
    read that as deliberate.
    """
    assert harness_only not in _skeleton()
    # The path, not the word: the skeleton explains in a comment why it moved.
    assert '"$HOME/.claude' not in _skeleton()


def _place(**overrides) -> MachinePlace:
    return MachinePlace(
        **{
            "home": "$HOME/.cheese/home/P/R",
            "workdir": "$HOME/.cheese/home/P/R/work",
            "state": "$HOME/.cheese/harness/P/R/x/deadbeef",
            "api_base": "https://cheese.example/api",
            "project_id": "P",
            "topic_id": "T",
            "agent_handle": "ops",
            "git_remote": "https://cheese.example/api/projects/P/git",
            **overrides,
        }
    )


@pytest.mark.parametrize(
    "plan",
    [
        pytest.param(ClaudeLaunch(system_prompt="房间的系统提示词"), id="claude-code"),
        pytest.param(
            PiLaunch(
                system_prompt="房间的系统提示词", model="glm-5.2", agent_handle="ops"
            ),
            id="pi",
        ),
    ],
)
def test_one_channel_carries_whichever_harness_it_was_handed(plan):
    """同一段 channel 逻辑，两个 harness —— 这是「切干净」的那句话本身。

    The platform half of the environment is the same sentence for both, and
    neither the composition nor anything it reads had to learn which one it is
    holding. A regression here does not look like a broken test elsewhere: it
    looks like the second harness never being reachable.
    """
    place = _place()
    command, env = machine_launcher.screen_launch(
        place,
        plan.on(place),
        hook_url="https://cheese.example/api/hooks/T",
        token="tok",
    )
    assert command[:2] == ["bash", "-lc"]
    # What the platform promises every session, regardless of what runs in it.
    assert env["CHEESE_HOME"] == place.home
    assert env["CHEESE_WORK"] == place.workdir
    assert env["CHEESE_TOKEN"] == "tok"
    assert env["CHEESE_API"] == place.api_base
    assert env["GIT_COMMITTER_NAME"] == "芝士"
    # And the platform's own half of the script, whoever filled the holes.
    for written in ("cheese-environment.py", "cheese-hook", "cheese-drain"):
        assert f'cat > "$HOME/.cheese/{written}"' in command[2]


def test_a_screen_with_no_room_context_is_given_none_rather_than_empty():
    """A probe or a fixture has no topic to name, and empty is not the same
    answer as absent: the launcher and the `cheese` CLI both branch on whether
    the variable is set at all."""
    bare = MachinePlace(
        home="/h",
        workdir="/w",
        state="",
        api_base="",
        project_id="",
        topic_id="",
        agent_handle="",
    )
    env = machine_launcher.screen_env(bare, hook_url="http://h", token="t")
    assert not {"CHEESE_API", "CHEESE_PROJECT", "CHEESE_TOPIC", "CHEESE_AUTHOR"} & set(
        env
    )
    assert "CHEESE_EXECUTION_TARGET" not in env

    placed = machine_launcher.screen_env(_place(), hook_url="http://h", token="t")
    assert placed["CHEESE_TOPIC"] == "T"


def test_the_two_harnesses_do_not_produce_the_same_launch():
    """The parametrised test above would pass just as well if `on` ignored the
    plan, so this is the half that says the answers actually differ."""
    place = _place()
    claude = ClaudeLaunch(system_prompt="x").on(place)
    pi = PiLaunch(system_prompt="x", model="glm-5.2").on(place)
    assert claude.command != pi.command
    assert "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH" in claude.env
    assert pi.env == {}


# --- the machine's document toolchain ---------------------------------------
#
# These are capabilities rather than dependencies, which decides every assertion
# below: the placement may not fail a launch, may not delay one, and belongs to
# the machine rather than to the room that happened to start first.


def _fake_upstream(tmp_path):
    """A `curl` on PATH serving real archives for this repo's pinned tools.

    Real archives, not stubs: the launcher finds the binary by name inside
    whatever the vendor packed, so a test that handed it a bare file would skip
    the step most likely to break on a version bump.
    """
    import tarfile

    served = tmp_path / "served"
    served.mkdir()
    payload = tmp_path / "payload"
    for tool in ("typst", "pandoc", "uv"):
        directory = payload / f"{tool}-some-vendor-layout"
        directory.mkdir(parents=True)
        (directory / tool).write_text(f"#!/bin/sh\necho {tool}\n")
        (directory / tool).chmod(0o755)
        with tarfile.open(served / tool, "w:gz") as tar:
            tar.add(directory, arcname=directory.name)
    (served / "font-sans").write_bytes(b"OTTO sans")
    (served / "font-serif").write_bytes(b"OTTO serif")

    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    log = tmp_path / "curl.log"
    curl = bin_dir / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        'out=""; url=""\n'
        "while [ $# -gt 0 ]; do\n"
        '  case "$1" in\n'
        '    -o) out="$2"; shift 2 ;;\n'
        '    http*) url="$1"; shift ;;\n'
        "    *) shift ;;\n"
        "  esac\n"
        "done\n"
        f'printf "%s\\n" "$url" >> "{log}"\n'
        'tool="${url%/*}"; tool="${tool%/*}"; tool="${tool##*/}"\n'
        f'[ -f "{served}/$tool" ] || exit 22\n'
        f'cp "{served}/$tool" "$out"\n'
    )
    curl.chmod(0o755)
    return bin_dir, log


def _agent_waiting_for(tmp_path, target, report, variable):
    """A harness that waits for the detached placement, then records what it saw.

    The launch deliberately does not wait for the toolchain, so the test needs
    something that does — and an agent reaching for a tool it wants is exactly
    what a real room does.
    """
    agent = tmp_path / f"agent-{report.name}.sh"
    agent.write_text(
        "#!/bin/sh\n"
        "i=0\n"
        f'while [ $i -lt 150 ] && [ ! -e "{target}" ]; do i=$((i+1)); sleep 0.1; done\n'
        f'printf "%s" "${variable}" > "{report}"\n'
    )
    agent.chmod(0o755)
    return f'AGENT="{agent}"\n'


def _machine_with_upstream(tmp_path):
    home, work, env = _machine(tmp_path)
    bin_dir, log = _fake_upstream(tmp_path)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["CHEESE_API"] = "http://cheese.test/api"
    return home, env, log


def test_the_toolchain_belongs_to_the_machine_not_to_the_room(tmp_path):
    """Every room on a machine shares one copy, and it outlives all of them.

    A per-room copy would refetch for each room and then be thrown away with
    that room's session home.
    """
    home, env, _log = _machine_with_upstream(tmp_path)
    session = home / "rooms" / "one"
    session.mkdir(parents=True)
    env = {**env, "CHEESE_HOME": str(session)}

    chain = home / ".cheese" / "toolchain"
    report = tmp_path / "chain"
    prepare = _agent_waiting_for(
        tmp_path, chain / "bin" / "typst", report, "CHEESE_TOOLCHAIN"
    )
    result = _run(tmp_path, env, prepare=prepare, command='"$AGENT"')

    assert result.returncode == 0, result.stderr
    assert report.read_text() == str(chain), "the tools live in the machine's home"
    assert (chain / "bin" / "typst").exists()
    # Version-named, so a bump lands beside the old copy instead of over it.
    assert (chain / "typst" / machine_launcher.toolchain.TYPST_VERSION).is_dir()
    assert not (session / ".cheese" / "toolchain").exists()


def test_a_toolchain_that_cannot_be_fetched_never_fails_the_launch(tmp_path):
    """A room that only answers a question needs none of these tools. Upstream
    being down must not be the reason nobody can talk to it."""
    home, work, env = _machine(tmp_path)
    bin_dir = tmp_path / "deadbin"
    bin_dir.mkdir()
    (bin_dir / "curl").write_text("#!/bin/sh\nexit 7\n")
    (bin_dir / "curl").chmod(0o755)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["CHEESE_API"] = "http://cheese.test/api"

    ran = tmp_path / "agent.ran"
    result = _run(
        tmp_path,
        env,
        prepare=_harness(tmp_path, f'touch "{ran}"\n'),
        command='"$AGENT"',
    )

    assert result.returncode == 0, result.stderr
    assert ran.exists(), "the room still runs without its document tools"


def test_a_tool_already_on_the_machine_is_not_fetched_again(tmp_path):
    """The placement runs on EVERY launch, so anything but a cheap no-op would
    re-download the whole toolchain for every room start on that machine."""
    home, env, log = _machine_with_upstream(tmp_path)

    chain = home / ".cheese" / "toolchain"
    for tool, version, kind, name in machine_launcher.toolchain.PLACEMENTS:
        if kind == "font":
            target = chain / "fonts" / machine_launcher.toolchain.fonts_pin()
        else:
            target = chain / tool / version
        target.mkdir(parents=True, exist_ok=True)
        (target / name).write_text("already here")
        (target / name).chmod(0o755)

    ran = tmp_path / "agent.ran"
    result = _run(
        tmp_path,
        env,
        prepare=_harness(tmp_path, f'sleep 1; touch "{ran}"\n'),
        command='"$AGENT"',
    )

    assert result.returncode == 0, result.stderr
    assert ran.exists()
    assert not log.exists(), "a placed toolchain must not be fetched again"


def test_tearing_a_screen_down_does_not_wait_for_the_toolchain(tmp_path):
    """`cleanup` ends in a bare `wait`, which waits for every CHILD of the
    launcher. A fetch started as an ordinary background job becomes one, so
    shutting a screen down would block on a download nobody was waiting for —
    it spent the whole 20s teardown budget before this was a double fork.
    """
    home, work, env = _machine(tmp_path)
    bin_dir = tmp_path / "slowbin"
    bin_dir.mkdir()
    (bin_dir / "curl").write_text("#!/bin/sh\nsleep 60\n")
    (bin_dir / "curl").chmod(0o755)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["CHEESE_API"] = "http://cheese.test/api"

    launcher = tmp_path / "launch.sh"
    launcher.write_text(
        machine_launcher.launch_script(
            prepare=_harness(tmp_path, "sleep 60\n"), command='"$AGENT"'
        )
    )
    process = subprocess.Popen(
        ["sh", str(launcher)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        time.sleep(1.0)  # long enough for the fetch to be under way
        process.terminate()
        # Ten seconds is already generous; the failure this guards against was
        # unbounded, held open by a curl with its own minutes-long timeout.
        process.wait(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
            pytest.fail("the launcher waited for the toolchain fetch")


def test_typst_is_pointed_at_the_fonts_we_ship(tmp_path):
    """With no CJK face typst exits 0 and writes a PDF of ordinary size in which
    every Chinese character is an empty box. Nothing the platform can check sees
    that, so the fonts have to be where typst looks without being asked."""
    home, env, _log = _machine_with_upstream(tmp_path)

    pin = machine_launcher.toolchain.fonts_pin()
    fonts = home / ".cheese" / "toolchain" / "fonts" / pin
    report = tmp_path / "fontpaths"
    prepare = _agent_waiting_for(
        tmp_path, fonts / "NotoSerifSC-VF.otf", report, "TYPST_FONT_PATHS"
    )
    result = _run(tmp_path, env, prepare=prepare, command='"$AGENT"')

    assert result.returncode == 0, result.stderr
    assert report.read_text() == str(fonts)
    assert (fonts / "NotoSansSC-VF.otf").exists()
    assert (fonts / "NotoSerifSC-VF.otf").exists()
