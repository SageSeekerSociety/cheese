"""The acceptance fixture's room home has to survive the launcher's narrowing.

A device case runs through the real launcher, and #4985 makes the launcher keep
the session to its own room of the machine (`CHEESE_HOMES_ROOT`, exported as
the room home's grandparent) and to its own seat. The session is refused before
it starts when a path it needs falls under that root but outside the room — so
a fixture that lays the room out beside the machine owner's home rather than
below its `home` takes the pinned claude with it and the case dies on the first
turn.

These cases ask the product's own judgment (`namespace._unnarrowable`) about
the fixture's layout, which runs anywhere; the namespace itself needs Linux
user namespaces and is only entered by the acceptance job.
"""

import importlib.util
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts/remote_execution"
SOURCE = ROOT / "backend/app/domain/agent/harness/claude_code/remote_execution"


def _acceptance():
    sys.path.insert(0, str(SCRIPTS))
    try:
        import acceptance

        return acceptance
    finally:
        sys.path.remove(str(SCRIPTS))


def _namespace():
    spec = importlib.util.spec_from_file_location(
        "acceptance_room_namespace", SOURCE / "namespace.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _homes_root(home: Path, owner: Path) -> Path:
    """`CHEESE_HOMES_ROOT` as the launcher's configure hole exports it.

    Read by running the hole rather than by repeating its expression: the
    root is the launcher's to name, and this case is about the two agreeing.
    """
    from app.domain.agent.harness.claude_code import device_launch

    holes = device_launch.launch_holes(state="$HOME/.cheese/state/acceptance")
    script = owner.parent / "configure.sh"
    script.write_text(
        holes.configure + '\nprintf "%s\\n" "$CHEESE_SEATS_ROOT" "$CHEESE_HOMES_ROOT"\n'
    )
    result = subprocess.run(
        ["sh", str(script)],
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(home),
            "REAL_HOME": str(owner),
        },
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return Path(result.stdout.splitlines()[-1])


def _owner_with_the_pinned_build(folder: Path) -> tuple[Path, Path]:
    owner = folder / "device-owner"
    pin = owner / ".cheese/claude/versions/2.1.299"
    pin.parent.mkdir(parents=True)
    pin.write_text("#!/bin/sh\nexit 0\n")
    pin.chmod(0o755)
    return owner, pin


def _needed(home: Path, pin: Path) -> list[str]:
    """The paths a device session needs, as `client.py` hands them over: its
    own home, the config it derives beside its seat, and the pinned build the
    launcher runs under the machine owner's home."""
    return [str(home), str(home / ".claude"), str(pin)]


def test_the_device_room_is_kept_to_itself_and_takes_nothing_else_along(tmp_path):
    acceptance = _acceptance()
    home = acceptance.case_home(tmp_path, SimpleNamespace(launcher="device"))
    home.mkdir(parents=True)
    owner, pin = _owner_with_the_pinned_build(tmp_path)

    root = _homes_root(home, owner)

    assert root == owner / ".cheese/home", textwrap.dedent(
        f"""\
        A room's home is `<homes>/<project>/<room>` on a machine, so the root the
        launcher derives for the session is the machine owner's `.cheese/home`,
        not the folder this case happens to use: {home}"""
    )
    assert home.is_relative_to(root)
    assert not pin.is_relative_to(root)
    assert (
        _namespace()._unnarrowable([[str(root), str(home)]], _needed(home, pin)) is None
    )


def test_a_room_beside_the_owner_would_take_the_pinned_build_with_it(tmp_path):
    """The layout the fixture used before this: beside the owner's home. The
    root that encloses it encloses the pinned build too, which is the refusal
    the device-launcher case died on."""
    home = tmp_path / "device-home"
    home.mkdir()
    owner, pin = _owner_with_the_pinned_build(tmp_path)

    problem = _namespace()._unnarrowable(
        [[str(tmp_path), str(home)]], _needed(home, pin)
    )

    assert problem is not None and "beside" in problem, problem
