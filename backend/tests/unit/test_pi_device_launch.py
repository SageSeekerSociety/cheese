"""pi 在会话机器上起来：装、铺、跑，一个真的 shell 跑一遍。

The launcher is a shell script that runs on somebody else's machine, so these
drive the actual script with a stand-in ``pi`` on the pinned path — what a
machine that already has the right version does. Nothing here reaches the
network, and nothing here needs the real pi installed.
"""

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from app.domain.agent.harness.pi import device_launch
from app.domain.agent.harness.pi.device_launch import PiLaunch, build_launch_script

FAKE = Path(__file__).resolve().parents[1] / "support/fake_pi.py"
FIXTURE = Path(__file__).parent / "fixtures/pi-entries.json"
STATE = "$HOME/.cheese/harness/proj/res/pi/deadbeef"


def _machine(tmp_path, *, pinned=True, npm=True):
    """An owner's home with the session's home inside it, plus a PATH."""
    owner = tmp_path / "owner"
    session = owner / "session"
    (session / "work").mkdir(parents=True)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    if pinned:
        binary = owner / ".cheese/tools/pi/node_modules/.bin/pi"
        binary.parent.mkdir(parents=True)
        binary.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "--version" ]; then\n'
            f'  echo "{device_launch.VERSION}"; exit 0\n'
            "fi\n"
            f'exec {sys.executable} {FAKE} {FIXTURE} "$@"\n'
        )
        binary.chmod(0o755)
    attempted = tmp_path / "npm.calls"
    if npm:
        stub = bindir / "npm"
        stub.write_text(f'#!/bin/sh\necho "$@" >> "{attempted}"\nexit 1\n')
        stub.chmod(0o755)
    else:
        # A machine that cannot install pi — no npm, or an npm that cannot reach
        # the registry. The launcher answers both the same way, and this stub is
        # also what keeps the suite off the network.
        stub = bindir / "npm"
        stub.write_text("#!/bin/sh\necho 'npm: offline' >&2\nexit 1\n")
        stub.chmod(0o755)
    env = {
        "PATH": f"{bindir}:{os.path.dirname(sys.executable)}:/usr/bin:/bin",
        "HOME": str(owner),
        "CHEESE_HOME": str(session),
        "CHEESE_WORK": str(session / "work"),
        "CHEESE_TOPIC": "11111111-1111-1111-1111-111111111111",
        "CHEESE_PROJECT": "22222222-2222-2222-2222-222222222222",
        "CHEESE_HOOK_URL": "http://127.0.0.1:1/hooks",
        "CHEESE_TOKEN": "scoped-token-value",
        "CHEESE_TOKEN_EXPIRES": str(int(time.time()) + 3600),
    }
    return owner, session, env, attempted


def _launch(**overrides) -> PiLaunch:
    return PiLaunch(
        **{
            "system_prompt": "平台系统提示词。$HOME 和 `backtick` 要原样留着。",
            "state": STATE,
            "api_base": "https://cheese.example/api",
            "model": "glm-5.2",
            "agent_handle": "pi",
            **overrides,
        }
    )


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
    script.write_text(build_launch_script(launch))
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
    assert not attempted.exists(), "a machine on the pin must not run npm"


def test_the_room_token_is_named_never_written(tmp_path):
    """The scoped credential is the room's, and the machine is not ours."""
    script = build_launch_script(_launch())
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


def test_a_machine_that_cannot_install_pi_says_so_instead_of_starting_nothing(
    tmp_path,
):
    _owner, _session, env, _ = _machine(tmp_path, pinned=False, npm=False)
    # A device runs a shipped FILE: this script is megabytes, and argv is not.
    script = tmp_path / "launch.sh"
    script.write_text(build_launch_script(_launch()))
    result = subprocess.run(
        ["sh", str(script)],
        env=env,
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0
    assert "could not install" in result.stdout + result.stderr


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
