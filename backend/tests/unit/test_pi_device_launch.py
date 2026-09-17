"""pi 在会话机器上起来：装、铺、跑，一个真的 shell 跑一遍。

The launcher is a shell script that runs on somebody else's machine, so these
drive the actual script with a stand-in ``pi`` on the pinned path — what a
machine that already has the right version does. Nothing here reaches the
network, and nothing here needs the real pi installed.
"""

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import uuid
from pathlib import Path

import pytest

from app.domain.agent.harness.launch import MachinePlace
from app.domain.agent.harness.pi import device_launch
from app.domain.agent.harness.pi.device_launch import PiLaunch, build_launch_script
from app.domain.machine import pi_dist

FAKE = Path(__file__).resolve().parents[1] / "support/fake_pi.py"
FIXTURE = Path(__file__).parent / "fixtures/pi-entries.json"
STATE = "$HOME/.cheese/harness/proj/res/pi/deadbeef"


def _pi_program() -> str:
    """A stand-in `pi`: answers --version, otherwise replays the fixture."""
    return (
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then\n'
        f'  echo "{device_launch.VERSION}"; exit 0\n'
        "fi\n"
        f'exec {sys.executable} {FAKE} {FIXTURE} "$@"\n'
    )


def _release(tmp_path) -> Path:
    """What the platform serves: the vendor's tarball shape, with a fake pi.

    ``pi/`` at the top and the binary beside its assets, because the launcher
    strips that first component — an archive of a bare file would unpack to
    something the version path then holds and nothing can run.
    """
    tree = tmp_path / "release/pi"
    (tree / "theme").mkdir(parents=True)
    (tree / "pi").write_text(_pi_program())
    (tree / "pi").chmod(0o755)
    (tree / "theme/default.json").write_text("{}")
    archive = tmp_path / "pi-release.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(tree, arcname="pi")
    return archive


def _machine(tmp_path, *, pinned=True, serves=None):
    """An owner's home with the session's home inside it, plus a PATH.

    ``serves`` is the archive the platform hands this machine when it asks for
    the pin; None is a machine that cannot get one. Either way the stub keeps
    the suite off the network — and passes everything that is not the pin
    download through to the real curl, which the event hooks use.
    """
    owner = tmp_path / "owner"
    session = owner / "session"
    (session / "work").mkdir(parents=True)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    if pinned:
        binary = owner / f".cheese/tools/pi/{device_launch.VERSION}/pi"
        binary.parent.mkdir(parents=True)
        binary.write_text(_pi_program())
        binary.chmod(0o755)
    attempted = tmp_path / "download.calls"
    served = (
        f'cp "{serves}" "$out"' if serves else "echo 'no route to the platform' >&2"
    )
    stub = bindir / "curl"
    stub.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        f'  *"/connector/pi/"*) ;;\n'
        f'  *) exec {shutil.which("curl")} "$@" ;;\n'
        "esac\n"
        "out=\n"
        'for arg in "$@"; do\n'
        '  case "$arg" in\n'
        "    -o) out=NEXT ;;\n"
        '    http*) url="$arg" ;;\n'
        '    *) [ "$out" = NEXT ] && out="$arg" ;;\n'
        "  esac\n"
        "done\n"
        f'printf "%s\\n" "$url" >> "{attempted}"\n'
        f"{served}\n"
    )
    stub.chmod(0o755)
    env = {
        "PATH": f"{bindir}:{os.path.dirname(sys.executable)}:/usr/bin:/bin",
        "HOME": str(owner),
        "CHEESE_HOME": str(session),
        "CHEESE_WORK": str(session / "work"),
        "CHEESE_TOPIC": "11111111-1111-1111-1111-111111111111",
        "CHEESE_PROJECT": "22222222-2222-2222-2222-222222222222",
        "CHEESE_API": "https://cheese.example/api",
        "CHEESE_HOOK_URL": "http://127.0.0.1:1/hooks",
        "CHEESE_TOKEN": "scoped-token-value",
        "CHEESE_TOKEN_EXPIRES": str(int(time.time()) + 3600),
    }
    return owner, session, env, attempted


def _launch(**overrides) -> PiLaunch:
    return PiLaunch(
        **{
            "system_prompt": "平台系统提示词。$HOME 和 `backtick` 要原样留着。",
            "model": "glm-5.2",
            "agent_handle": "pi",
            **overrides,
        }
    )


def _place() -> MachinePlace:
    return MachinePlace(
        home="$HOME/session",
        workdir="$HOME/session/work",
        store="$HOME/.cheese/store/proj",
        state=STATE,
        api_base="https://cheese.example/api",
        project_id="proj",
        topic_id="topic",
        agent_handle="pi",
    )


def _script(launch: PiLaunch) -> str:
    return build_launch_script(launch, _place())


def _state_of(owner: Path) -> Path:
    return owner / ".cheese/harness/proj/res/pi/deadbeef"


def _socket_of(state: Path) -> str:
    """What the CONNECTOR derives, spelled out here rather than imported.

    ``cli/internal/host/executor.go`` resolves the recorded state directory and
    names the socket from its digest; a test that called our own helper would
    agree with itself while the two ends drifted apart.
    """
    digest = hashlib.sha256(os.path.realpath(state).encode()).hexdigest()[:24]
    return f"/tmp/cheese-execution-{os.getuid()}-{digest}.sock"


def _start(tmp_path, env, launch: PiLaunch):
    script = tmp_path / "launch.sh"
    script.write_text(_script(launch))
    return subprocess.Popen(
        ["sh", str(script)],
        env=env,
        cwd=str(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _await_socket(path: str, process, deadline: float = 25) -> None:
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if os.path.exists(path):
            return
        if process.poll() is not None:
            pytest.fail(f"launcher exited early:\n{process.stdout.read()}")
        time.sleep(0.05)
    process.kill()
    pytest.fail(f"runner never bound its socket:\n{process.stdout.read()}")


def _call(path: str, method: str, params: dict | None = None) -> dict:
    connection = socket.socket(socket.AF_UNIX)
    connection.settimeout(30)
    try:
        connection.connect(path)
        connection.sendall(
            json.dumps({"method": method, "params": params or {}}).encode() + b"\n"
        )
        with connection.makefile("rb") as stream:
            answer = json.loads(stream.readline())
    finally:
        connection.close()
    if "error" in answer:
        raise RuntimeError(answer["error"])
    return answer["result"]


def test_a_machine_that_already_has_the_pin_installs_nothing(tmp_path):
    owner, session, env, attempted = _machine(tmp_path)
    process = _start(tmp_path, env, _launch())
    state = _state_of(owner)
    try:
        _await_socket(_socket_of(state), process)
        # The backend's only address for this session is the one the connector
        # will compute from the state string it recorded.
        assert _call(_socket_of(state), "ping")["alive"] is True
        # pi's config landed inside the SESSION's home, not the owner's.
        models = json.loads((session / ".pi/agent/models.json").read_text())
        assert not (owner / ".pi").exists()
        assert models["providers"]["cheese"]["baseUrl"] == (
            "https://cheese.example/api/llm/v1"
        )
    finally:
        process.terminate()
        process.wait(timeout=20)
    assert not attempted.exists(), "a machine on the pin must not re-download it"


def test_the_room_token_is_named_never_written(tmp_path):
    """The scoped credential is the room's, and the machine is not ours."""
    script = _script(_launch())
    assert "$CHEESE_TOKEN" in script
    owner, session, env, _ = _machine(tmp_path)
    process = _start(tmp_path, env, _launch())
    state = _state_of(owner)
    try:
        _await_socket(_socket_of(state), process)
        planted = [
            path
            for path in list(session.rglob("*")) + list(state.rglob("*"))
            if path.is_file() and not path.name.endswith(".sqlite-wal")
        ]
        carrying = [
            path
            for path in planted
            if b"scoped-token-value" in path.read_bytes()
            # The drainer's own env file is how the platform's events are
            # delivered at all; it is not pi's copy of the credential.
            and path.name != "cheese-drain.env"
        ]
        assert carrying == []
    finally:
        process.terminate()
        process.wait(timeout=20)


def test_the_system_prompt_survives_the_shell_that_carried_it(tmp_path):
    owner, _session, env, _ = _machine(tmp_path)
    launch = _launch()
    process = _start(tmp_path, env, launch)
    state = _state_of(owner)
    try:
        _await_socket(_socket_of(state), process)
        assert (state / "system-prompt.md").read_text() == launch.system_prompt
    finally:
        process.terminate()
        process.wait(timeout=20)


def test_a_turn_reaches_pi_and_comes_back_stamped(tmp_path):
    owner, _session, env, _ = _machine(tmp_path)
    process = _start(tmp_path, env, _launch())
    state = _state_of(owner)
    path = _socket_of(state)
    try:
        _await_socket(path, process)
        work = str(uuid.uuid4())
        _call(
            path,
            "send",
            {"input_id": str(uuid.uuid4()), "text": "改一下", "work_id": work},
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            entries = _call(path, "entries")["entries"]
            if entries:
                break
            time.sleep(0.05)
        assert entries, "the turn produced no entries"
        assert {entry["cheese"]["work_id"] for entry in entries} == {work}
        assert {entry["cheese"]["harness"] for entry in entries} == {"pi"}
    finally:
        process.terminate()
        process.wait(timeout=20)


def test_a_machine_without_the_pin_takes_it_from_the_platform(tmp_path):
    """The whole point of #1035: the machine asks US, and asks for the pin.

    A URL this launcher builds that the serving route would reject is the
    failure that matters — it would 400 every machine that has no pi yet — so
    the two ends are checked against each other here.
    """
    owner, _session, env, attempted = _machine(
        tmp_path, pinned=False, serves=_release(tmp_path)
    )
    process = _start(tmp_path, env, _launch())
    try:
        _await_socket(_socket_of(_state_of(owner)), process)
    finally:
        process.terminate()
        process.wait(timeout=20)
    asked = attempted.read_text().split()
    assert len(asked) == 1, asked
    prefix, _, rest = asked[0].partition(f"/connector/pi/{device_launch.VERSION}/")
    assert prefix == "https://cheese.example/api", asked[0]
    platform, _, name = rest.partition("/")
    assert name == "pi.tar.gz", asked[0]
    assert pi_dist.PLATFORM_RE.match(platform), platform

    root = owner / f".cheese/tools/pi/{device_launch.VERSION}"
    assert (root / "pi").is_file(), "the pin is not where the launcher looks"
    # The assets ship beside the binary and pi reads them from there, so an
    # install that kept only the executable would start and then misbehave.
    assert (root / "theme/default.json").is_file()
    # Nothing half-unpacked is left where a later launch would accept it.
    assert [p.name for p in root.parent.iterdir()] == [device_launch.VERSION]


def test_a_machine_that_cannot_reach_the_platform_says_so_instead_of_starting(
    tmp_path,
):
    _owner, _session, env, _ = _machine(tmp_path, pinned=False)
    # A device runs a shipped FILE: this script is megabytes, and argv is not.
    script = tmp_path / "launch.sh"
    script.write_text(_script(_launch()))
    result = subprocess.run(
        ["sh", str(script)],
        env=env,
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0
    assert "could not fetch pi" in result.stdout + result.stderr


def test_the_state_directory_is_the_owners_not_the_sessions(tmp_path):
    """``$HOME`` in the state string is the CONNECTOR's placeholder.

    Expanded by the session's own shell it would name the isolated home, which
    is rebuilt whenever a screen is — taking the runner's journal, its record of
    which inputs were already accepted, and the socket's identity with it.
    """
    owner, session, env, _ = _machine(tmp_path)
    process = _start(tmp_path, env, _launch())
    try:
        _await_socket(_socket_of(_state_of(owner)), process)
        assert not (session / ".cheese/harness").exists()
    finally:
        process.terminate()
        process.wait(timeout=20)


@pytest.mark.parametrize(
    "flag", ["--no-skills", "--no-extensions", "--no-prompt-templates", "--no-themes"]
)
def test_the_owners_own_files_do_not_reach_the_room(flag):
    """``PI_CODING_AGENT_DIR`` moves pi's config and nothing else.

    Verified against pi 0.85.1 on 2026-09-14: with the config dir already
    pointed at the session's home, a plain run still discovered the machine
    owner's ``~/.agents/skills`` and pasted their SKILL.md files into the system
    prompt. These four flags are what actually draws the boundary.
    """
    assert flag in _launch().arguments()


def test_the_gateway_is_described_as_the_plain_openai_shape_it_is():
    """量过才写的：2026-09-15 一轮真活里，不带 compat 时 pi 发出去的是
    `store` 和 `max_completion_tokens` —— 而网关后面那个 GLM 端点自己声明的是
    `supportsStore: false`、`maxTokensField: max_tokens`。那一轮没报错，但
    「没报错」不是「支持」。"""
    declared = json.loads(device_launch.provider("https://c.example/api", "glm-5.2"))
    compat = declared["providers"]["cheese"]["compat"]
    assert compat["supportsStore"] is False
    assert compat["maxTokensField"] == "max_tokens"
    # A reasoning model would otherwise be handed a `developer` role.
    assert compat["supportsDeveloperRole"] is False


def test_a_runner_that_dies_on_the_way_up_leaves_its_reason_on_the_machine(tmp_path):
    """The runner IS the screen's program, so unredirected its stderr goes to a
    tmux pane — and a runner that fails on the way up takes that pane with it.

    What is left for anyone to find is the socket it never bound: a turn reports
    `dial unix …: no such file or directory`, which names the consequence and
    nothing else. pi's own stderr had a file the whole time; the runner's did
    not, and on dev 2026-09-15 that cost an afternoon of probes against the box.
    """
    owner, _session, env, _ = _machine(tmp_path)
    # A pi on the pinned path that passes the version check and then refuses to
    # speak: the runner gets as far as starting it and no further.
    binary = owner / f".cheese/tools/pi/{device_launch.VERSION}/pi"
    binary.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then\n'
        f'  echo "{device_launch.VERSION}"; exit 0\n'
        "fi\n"
        'echo "this pi will not start here" >&2\n'
        "exit 3\n"
    )
    binary.chmod(0o755)
    process = _start(tmp_path, env, _launch())
    state = _state_of(owner)
    try:
        process.wait(timeout=60)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=20)
    assert not os.path.exists(_socket_of(state)), "nothing may be listening there"
    assert (state / "runner.log").read_text().strip(), (
        "the machine kept no record of why the runner did not come up"
    )


def test_the_platforms_own_skills_reach_the_room(tmp_path):
    """`--no-skills` shuts out the owner's files; the platform's come back by name.

    Both halves matter and they are easy to confuse. Dropping the flag lets a
    room read whatever the person who lent us the machine keeps in ``~/.agents``;
    dropping the explicit paths leaves the room told to load skills that are not
    there — the guide names ``cheese-docs`` on every turn.
    """
    owner, _session, env, _ = _machine(tmp_path)
    recorded = tmp_path / "pi-argv.json"
    env["PI_FAKE_ARGV"] = str(recorded)
    launch = _launch()
    process = _start(tmp_path, env, launch)
    state = _state_of(owner)
    try:
        _await_socket(_socket_of(state), process)
        argv = json.loads(recorded.read_text())

        assert "--no-skills" in argv, "the owner's own files stay out"
        named = [argv[i + 1] for i, item in enumerate(argv) if item == "--skill"]
        assert named, "the platform's skills have to be named to be loaded"
        for directory in named:
            body = Path(directory) / "SKILL.md"
            assert body.is_file(), f"{directory} was named but not written"
            assert body.read_text().startswith("---"), "a skill needs its frontmatter"
        # Whatever the platform ships, all of it arrives — a skill present on one
        # harness and missing on the other is the split nobody notices.
        assert {Path(d).name for d in named} == {
            name.split("/")[1] for name in launch.configuration()["skills"]
        }
    finally:
        process.terminate()
        process.wait(timeout=20)
