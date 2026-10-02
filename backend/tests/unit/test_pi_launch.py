"""pi 在中心机上起来：装、配、跑，真跑一遍那段脚本。

The launch is a Python program the session host runs over the connector's stdin
(`launch.script`), and it leaves a runner running there (`host.configure`).
These run that program as the host would, with a stand-in `pi` on the pinned
path or served by a stand-in platform. Nothing here reaches the network, and
nothing here needs the real pi installed.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest

from app.domain.agent.harness.pi import launch
from app.domain.agent.harness.pi.host import ping
from app.domain.machine import pi_dist
from tests.support.room_machine import NO_MACHINE

FAKE = Path(__file__).resolve().parents[1] / "support/fake_pi.py"
FIXTURE = Path(__file__).parent / "fixtures/pi-entries.json"
STATE = "$HOME/.cheese/harness/proj/res/pi/deadbeef"
API = "https://cheese.example/api"
TOKEN = "scoped-token-value"


def _pi_program() -> str:
    """A stand-in `pi`: answers --version, otherwise replays the fixture."""
    return (
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then\n'
        f'  echo "{launch.VERSION}"; exit 0\n'
        "fi\n"
        f'exec {sys.executable} {FAKE} {FIXTURE} "$@"\n'
    )


def _release(tmp_path) -> Path:
    """What the platform serves: the vendor's tarball shape, with a fake pi."""
    tree = tmp_path / "release/pi"
    (tree / "theme").mkdir(parents=True)
    (tree / "pi").write_text(_pi_program())
    (tree / "pi").chmod(0o755)
    (tree / "theme/default.json").write_text("{}")
    archive = tmp_path / "pi-release.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(tree, arcname="pi")
    return archive


class Host:
    """The session host's account: its home, a PATH whose `curl` is a stand-in
    for the platform's pin route, and every runner a launch left behind."""

    def __init__(self, tmp_path: Path, *, pinned=True, serves=None):
        self.home = tmp_path / "host"
        self.home.mkdir()
        bindir = tmp_path / "bin"
        bindir.mkdir()
        if pinned:
            binary = self.home / f".cheese/tools/pi/{launch.VERSION}/pi"
            binary.parent.mkdir(parents=True)
            binary.write_text(_pi_program())
            binary.chmod(0o755)
        self.attempted = tmp_path / "download.calls"
        served = (
            f'cp "{serves}" "$out"' if serves else "echo 'no route to the platform' >&2"
        )
        (bindir / "curl").write_text(
            "#!/bin/sh\n"
            "out=\n"
            'for arg in "$@"; do\n'
            '  case "$arg" in\n'
            "    -o) out=NEXT ;;\n"
            '    http*) url="$arg" ;;\n'
            '    *) [ "$out" = NEXT ] && out="$arg" ;;\n'
            "  esac\n"
            "done\n"
            f'printf "%s\\n" "$url" >> "{self.attempted}"\n'
            f"{served}\n"
        )
        (bindir / "curl").chmod(0o755)
        self.env = {
            "PATH": f"{bindir}:{os.path.dirname(sys.executable)}:/usr/bin:/bin",
            "HOME": str(self.home),
        }
        self.state = Path(STATE.replace("$HOME", str(self.home)))

    def launch(self, *, model="glm-5.2", prompt="System", resume=None, ship=True):
        program = launch.on_host(
            state=STATE,
            config={
                "opening": {
                    "system_prompt": prompt,
                    "resume_token": resume,
                    "model": model,
                    "agent_handle": "cheese",
                },
                "args": launch.arguments(model),
                "execution_target": NO_MACHINE,
                "skills": {},
                "extension": {"index.ts": "export default function () {}\n"},
                "notice": "",
            },
            api_base=API,
            model=model,
            env={"CHEESE_TOKEN": TOKEN, "CHEESE_API": API},
        ).program(ship=ship)
        return subprocess.run(
            [sys.executable, "-"],
            input=program,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def stop(self) -> None:
        status = ping(self.state) if self.state.exists() else None
        if status:
            os.kill(int(status["pid"]), signal.SIGTERM)


@pytest.fixture
def host(tmp_path):
    made: list[Host] = []

    def build(**kwargs) -> Host:
        made.append(Host(tmp_path, **kwargs))
        return made[-1]

    yield build
    for one in made:
        one.stop()


def started(result) -> dict:
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_a_host_without_the_pin_takes_it_from_the_platform(tmp_path, host):
    machine = host(pinned=False, serves=_release(tmp_path))
    status = started(machine.launch())
    assert status["alive"] is True
    asked = machine.attempted.read_text().split()
    assert len(asked) == 1, asked
    prefix, _, rest = asked[0].partition(f"/connector/pi/{launch.VERSION}/")
    assert prefix == API, asked[0]
    platform, _, name = rest.partition("/")
    assert name == "pi.tar.gz", asked[0]
    assert pi_dist.PLATFORM_RE.match(platform), platform
    root = machine.home / f".cheese/tools/pi/{launch.VERSION}"
    assert (root / "pi").is_file(), "the pin is not where the launch looks"
    # The assets ship beside the binary and pi reads them from there.
    assert (root / "theme/default.json").is_file()
    # Nothing half-unpacked is left where a later launch would accept it.
    assert [p.name for p in root.parent.iterdir()] == [launch.VERSION]


def test_a_host_that_cannot_reach_the_platform_says_so_instead_of_starting(host):
    result = host(pinned=False).launch()
    assert result.returncode != 0
    assert "could not fetch pi" in result.stdout + result.stderr


def test_the_room_token_is_named_never_written(host):
    machine = host()
    started(machine.launch())
    models = json.loads((machine.state / "agent/models.json").read_text())
    assert models["providers"]["cheese"]["apiKey"] == "$CHEESE_TOKEN"
    for path in machine.state.rglob("*"):
        if path.is_file() and path.suffix != ".pyz":
            assert TOKEN.encode() not in path.read_bytes(), path


def test_the_system_prompt_survives_the_trip(host):
    prompt = '说 $HOME 和 `rm -rf /` 和 \'; exit 1 #\n"引号"'
    machine = host()
    started(machine.launch(prompt=prompt))
    assert (machine.state / "system-prompt.md").read_text() == prompt


def test_the_same_launch_keeps_its_session_and_a_changed_one_resumes_it(host):
    machine = host()
    first = started(machine.launch())
    again = started(machine.launch(prompt="a different prompt"))
    # The room's system prompt is not part of what a running pi could not adopt.
    assert again["pid"] == first["pid"]
    changed = started(machine.launch(model="another-model", resume=first["session_id"]))
    assert changed["pid"] != first["pid"]
    assert changed["session_id"] == first["session_id"]
    deadline = time.monotonic() + 10
    while True:
        try:
            os.kill(first["pid"], 0)
        except ProcessLookupError:
            break
        assert time.monotonic() < deadline, "the replaced runner is still there"
        time.sleep(0.1)


def test_a_launch_without_the_runner_starts_only_where_the_host_holds_it(host):
    machine = host()
    missing = started(machine.launch(ship=False))
    assert missing == {"runner": "missing"}
    assert ping(machine.state) is None
    first = started(machine.launch())
    again = started(machine.launch(ship=False))
    assert again["alive"] is True
    assert again["pid"] == first["pid"]


def test_a_runner_that_dies_on_the_way_up_says_why(host):
    machine = host()
    shutil.rmtree(machine.home / ".cheese/tools/pi")
    broken = machine.home / f".cheese/tools/pi/{launch.VERSION}/pi"
    broken.parent.mkdir(parents=True)
    broken.write_text("#!/bin/sh\necho 'pi cannot open its session dir' >&2\nexit 3\n")
    broken.chmod(0o755)
    result = machine.launch()
    assert result.returncode != 0
    assert "pi" in result.stderr
    assert (machine.state / "runner.log").is_file()
