"""Enrollment must leave a machine that can run the harness it advertises.

The claude half of this argument is in ``test_enrollment_installs_claude``: a
machine with no agent enrolls cleanly, reports healthy, and then fails per-room
forever. pi arrived without that check (#1034), so a cloud node joined the pool
as pi capacity it had never been asked to prove.
"""

import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

from app.domain.agent.harness.pi import device_launch as pi_launch
from app.domain.machine import pi_dist
from app.domain.machine.enrollment import bootstrap_script

ORIGIN = "https://cheese.example.com"


def script() -> str:
    return bootstrap_script(origin=ORIGIN + "/", token="TOK", device_id="DEV")


def test_pi_is_fetched_from_the_platform_at_the_pinned_version():
    """The same route the launcher uses, so one machine cannot end up with a pi
    enrollment placed and the launcher then refuses."""
    s = script()
    assert f"{ORIGIN}/connector/pi/{pi_launch.VERSION}/" in s
    assert f'PI_BIN="$HOME/.cheese/tools/pi/{pi_launch.VERSION}/pi"' in s


def test_the_url_enrollment_builds_is_one_the_platform_serves():
    """A platform string this script invents that the route rejects would 400
    every enrollment, and only on machines of that shape."""
    s = script()
    for platform in ("linux-x64", "linux-arm64", "darwin-x64", "darwin-arm64"):
        assert pi_dist.PLATFORM_RE.match(platform)
    line = next(one for one in s.splitlines() if "/connector/pi/" in one)
    assert "$_piplat/pi.tar.gz" in line, line


def test_nothing_about_pi_depends_on_npm_or_the_public_registry():
    """The reason the platform serves it at all: a cloud node is on a private
    subnet and a self-hosted machine belongs to a user whose network we do not
    control. An `npm install` puts both, plus a node runtime we never asked the
    owner for, in front of every enrollment."""
    s = script()
    for outside in ("npm install", "registry.npmjs.org", "pi-coding-agent"):
        assert outside not in s, f"enrollment must not depend on {outside}"


def test_a_machine_that_cannot_run_pi_fails_enrollment():
    """Fatal, like tmux, git and claude above it.

    This is the one check that can fail where claude is fine — the vendor
    publishes no musl build — and hearing it here beats reading it out of the
    first room's launcher output.
    """
    s = script()
    block = s[s.index("PI_BIN=") : s.index("cheesehost.new")]
    for failure in ("no musl build", "could not fetch pi", "did not unpack"):
        assert failure in block, failure
    assert block.count("exit 1") >= 4


def _serve(bindir: Path, archive: Path) -> None:
    """A `curl` that hands the pin download this archive and passes the rest on.

    The stub is what keeps the suite off the network; passing everything else
    through matters because the launcher's own hooks post with curl too.
    """
    stub = bindir / "curl"
    stub.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        "  *'/connector/pi/'*) ;;\n"
        f'  *) exec {shutil.which("curl")} "$@" ;;\n'
        "esac\n"
        "out=\n"
        'for arg in "$@"; do\n'
        '  case "$arg" in\n'
        "    -o) out=NEXT ;;\n"
        "    http*) ;;\n"
        '    *) [ "$out" = NEXT ] && out="$arg" ;;\n'
        "  esac\n"
        "done\n"
        f'cp "{archive}" "$out"\n'
    )
    stub.chmod(0o755)


def _release(tmp_path: Path) -> Path:
    tree = tmp_path / "release/pi"
    tree.mkdir(parents=True)
    (tree / "pi").write_text(
        f'#!/bin/sh\n[ "$1" = "--version" ] && echo "{pi_launch.VERSION}"\n'
    )
    (tree / "pi").chmod(0o755)
    archive = tmp_path / "pi.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(tree, arcname="pi")
    return archive


def test_the_pin_lands_where_an_enrolled_machine_will_look_for_it(tmp_path):
    """Enrollment spells the owner's home ``$HOME`` and the launcher spells it
    ``$REAL_HOME``; a mismatch installs pi where nothing ever looks, and says so
    nowhere until a room fails to start. So run the thing.
    """
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _serve(bindir, _release(tmp_path))
    program = tmp_path / "install.sh"
    program.write_text(
        "set -eu\n" + pi_launch.install(home="$HOME", base=ORIGIN) + 'echo "$PI_BIN"\n'
    )
    result = subprocess.run(
        ["sh", str(program)],
        env={
            "HOME": str(home),
            "PATH": f"{bindir}:{os.path.dirname(sys.executable)}:/usr/bin:/bin",
        },
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    placed = Path(result.stdout.strip())
    assert placed.is_file() and os.access(placed, os.X_OK)
    # The launcher's own spelling of the same path, with the owner's home
    # resolved the way the launcher resolves it.
    assert (
        placed
        == Path(pi_launch.root("$REAL_HOME").replace("$REAL_HOME", str(home))) / "pi"
    )


def test_a_build_that_is_not_the_pin_is_thrown_away_rather_than_kept(tmp_path):
    """A download exiting 0 says nothing about what it produced. Leaving the
    wrong version at the version-named path would make every later launch skip
    the install and run it."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    tree = tmp_path / "wrong/pi"
    tree.mkdir(parents=True)
    (tree / "pi").write_text("#!/bin/sh\necho 0.0.1\n")
    (tree / "pi").chmod(0o755)
    archive = tmp_path / "wrong.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(tree, arcname="pi")
    _serve(bindir, archive)
    program = tmp_path / "install.sh"
    program.write_text("set -eu\n" + pi_launch.install(home="$HOME", base=ORIGIN))
    result = subprocess.run(
        ["sh", str(program)],
        env={"HOME": str(home), "PATH": f"{bindir}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode != 0
    assert "not 0.85.1" in result.stderr or pi_launch.VERSION in result.stderr
    assert not (home / ".cheese/tools/pi" / pi_launch.VERSION).exists()
