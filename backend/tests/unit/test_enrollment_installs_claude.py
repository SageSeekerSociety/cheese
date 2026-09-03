"""Enrollment must leave a machine that can actually run a turn.

A machine with no claude, or one below the launcher's floor, enrolls cleanly
and then fails per-topic forever — the launcher refuses the build and the
machine keeps reporting healthy. These guard the two halves of preventing that:
the binary is installed, and it comes from us rather than from a host the
machine may not be able to reach.
"""

from app.domain.agent.harness.claude_code import device_launch
from app.domain.machine.enrollment import bootstrap_script

ORIGIN = "https://cheese.example.com"


def script() -> str:
    return bootstrap_script(origin=ORIGIN, token="TOK", device_id="DEV")


def test_claude_is_installed_at_the_pinned_version():
    s = script()
    pin = device_launch.CLAUDE_PINNED_VERSION
    assert f"/connector/claude/{pin}/" in s
    # Laid out exactly where the launcher's pin looks for it.
    assert f'claude_pin="$HOME/.local/share/claude/versions/{pin}"' in s


def test_the_machine_owners_claude_is_left_alone():
    """A self-hosted machine belongs to a person.

    Repointing ~/.local/bin/claude would change which version they get when they
    type `claude` — on their own machine, without being asked, as a side effect
    of lending us capacity. The versions directory is built for coexistence, so
    adding one costs them nothing; the entry point is theirs.

    It also buys us nothing: the launcher looks in versions/<pin> first and
    never consults ~/.local/bin for the pin.
    """
    s = script()
    claude_block = s[s.index("claude_pin=") : s.index("cheesehost.new")]
    assert "ln -s" not in claude_block, "must not repoint the owner's claude"
    assert "$HOME/.local/bin/claude" not in claude_block


def test_the_pin_is_installed_even_when_a_newer_claude_exists():
    """Otherwise the platform rides whatever the owner happens to have, and
    their next upgrade becomes our behaviour change. Pinning has to mean the
    version we put there, not the version we found."""
    s = script()
    claude_block = s[s.index("claude_pin=") : s.index("cheesehost.new")]
    # The only condition guarding the download is the pin's own absence.
    assert 'if [ ! -x "$claude_pin" ]; then' in claude_block
    assert "command -v claude" not in claude_block


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


def test_the_floor_is_checked_against_what_we_placed():
    """Not against whatever `claude` resolves to — that is the owner's entry
    point and could be any version at all. A download exiting 0 is also not
    evidence about the version it produced, so this runs after placing it."""
    s = script()
    floor = device_launch.CLAUDE_MIN_VERSION
    assert '"$claude_pin" --version' in s
    assert f'"{floor}" "$have"' in s
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
    claude_block = s[s.index("claude_pin=") : s.index("cheesehost.new")]
    assert ">&2" in claude_block and "exit 1" in claude_block
