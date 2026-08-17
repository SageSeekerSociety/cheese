"""Enrollment must leave a machine that can actually run a turn.

A machine with no claude, or one below the launcher's floor, enrolls cleanly
and then fails per-topic forever — the launcher refuses the build and the
machine keeps reporting healthy. These guard the two halves of preventing that:
the binary is installed, and it comes from us rather than from a host the
machine may not be able to reach.
"""

from app.domain.agent import device_launch
from app.domain.machine.enrollment import bootstrap_script

ORIGIN = "https://cheese.example.com"


def script() -> str:
    return bootstrap_script(origin=ORIGIN, token="TOK", device_id="DEV")


def test_claude_is_installed_at_the_pinned_version():
    s = script()
    pin = device_launch.CLAUDE_PINNED_VERSION
    assert f"/connector/claude/{pin}/" in s
    # Laid out where the launcher's pin looks for it.
    assert f'"$HOME/.local/share/claude/versions/{pin}"' in s
    assert '"$HOME/.local/bin/claude"' in s


def test_the_binary_comes_from_the_platform_not_the_vendor():
    """The reason this route exists.

    A cloud node is on a private subnet, a self-hosted machine belongs to a user
    whose network we do not control, and the vendor's installer names regional
    unavailability as a failure mode. Fetching from the vendor would make
    enrollment depend on three things we cannot see.
    """
    s = script()
    assert f"{ORIGIN}/connector/claude/" in s
    for vendor in ["claude.ai/install.sh", "downloads.claude.ai"]:
        assert vendor not in s, f"enrollment must not depend on {vendor}"


def test_the_floor_is_enforced_before_and_after_installing():
    s = script()
    floor = device_launch.CLAUDE_MIN_VERSION
    # Twice: skip the download when a good-enough claude is already there, and
    # refuse to finish if what landed is still too old. Verifying after is not
    # redundant — the installer exiting 0 is not evidence about the version.
    assert s.count(f'"{floor}" "$have"') == 2
    assert "sort -V" in s
    assert "exit 1" in s


def test_versions_track_the_launcher_rather_than_being_restated():
    """A machine enrolled against a different number than the launcher enforces
    enrolls cleanly and runs nothing."""
    s = script()
    assert device_launch.CLAUDE_PINNED_VERSION in s
    assert device_launch.CLAUDE_MIN_VERSION in s


def test_musl_machines_get_a_musl_build():
    """Our own `<os>-<arch>` target names cannot express musl, and an Alpine-ish
    machine handed a glibc build fails at exec with nothing useful to read. Only
    the machine can tell, so the check runs there."""
    s = script()
    assert "linux-$carch-musl" in s
    assert "ldd /bin/ls" in s
    assert "darwin-$carch" in s


def test_claude_is_fatal_at_enrollment_like_tmux_and_git():
    """Same reasoning as the tools above it: a failure discovered later is a
    failure nobody attributes to enrollment."""
    s = script()
    claude_block = s[s.index("claude_ok=0") : s.index("cheesehost.new")]
    assert ">&2" in claude_block and "exit 1" in claude_block
