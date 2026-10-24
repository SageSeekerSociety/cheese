"""A long command that prints nothing is work, not a session stuck talking.

The runner ends work that talks without working: it said something within
``TALKING_S`` and has called no tool and finished nothing for
``no_progress_s``. A session that started a long command and has been quiet
since is not that, however long the command runs.
"""

from app.core.config import settings
from app.domain.agent.harness.driven import lifecycle

#: The clocks as the platform registers runners with it.
NO_PROGRESS_S = settings.agent_no_progress_s
SILENT_S = settings.agent_stuck_silence_s


def _watching() -> lifecycle.Watchdog:
    clocks = lifecycle.Watchdog(no_progress_s=NO_PROGRESS_S)
    clocks.started(0.0)
    return clocks


def test_a_quiet_long_command_is_not_ended_as_no_progress():
    clocks = _watching()
    clocks.output(1.0)
    clocks.tool_started("build", 2.0)

    # The command has run as long as the no-progress clock, and longer: the
    # session's last words were before it started.
    for now in (2.0 + NO_PROGRESS_S, 2.0 + 3 * NO_PROGRESS_S):
        assert clocks.fired(now) is None


def test_a_session_that_goes_on_talking_without_working_is_ended():
    clocks = _watching()
    clocks.output(1.0)
    clocks.output(1.0 + NO_PROGRESS_S)

    assert clocks.fired(1.0 + NO_PROGRESS_S) == lifecycle.NO_PROGRESS


def test_a_session_that_produces_nothing_at_all_is_silent():
    """Neither of the other clocks has anything to go on here: nothing was
    said lately and no input is waiting to be read. This one is what makes a
    build that stopped answering visible at all."""
    clocks = lifecycle.Watchdog(silent_s=SILENT_S)
    clocks.started(0.0)

    assert clocks.fired(SILENT_S - 1) is None
    assert clocks.fired(SILENT_S + 1) == lifecycle.SILENT


def test_a_quiet_long_command_does_not_trip_the_silence_clock():
    """A command in flight can be silent for as long as it likes: that is the
    build working, not the build dying."""
    clocks = lifecycle.Watchdog(silent_s=SILENT_S)
    clocks.started(0.0)
    clocks.tool_started("build", 1.0)

    for now in (1.0 + SILENT_S, 1.0 + 3 * SILENT_S):
        assert clocks.fired(now) is None


def test_the_silence_clock_starts_again_when_the_command_returns():
    clocks = lifecycle.Watchdog(silent_s=SILENT_S)
    clocks.started(0.0)
    clocks.tool_started("build", 1.0)
    clocks.tool_returned("build", 1.0 + 2 * SILENT_S)

    assert clocks.fired(1.0 + 2 * SILENT_S + SILENT_S - 1) is None
    assert clocks.fired(1.0 + 2 * SILENT_S + SILENT_S + 1) == lifecycle.SILENT
