"""A long command that prints nothing is work, not a session stuck talking.

The runner ends work that talks without working: it said something within
``TALKING_S`` and has called no tool and finished nothing for
``no_progress_s``. A session that started a long command and has been quiet
since is not that, however long the command runs.
"""

from app.core.config import settings
from app.domain.agent.harness.driven import lifecycle

#: The clock as the platform registers runners with it.
NO_PROGRESS_S = settings.agent_no_progress_s


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
