"""The Claude Code adapter, and the whole of what is private to it.

What is inside this package is how ONE harness happens to work: a runner that
holds a headless ``claude -p`` by its stream-json pipes, the translation of its
records into the room's events, the launcher that pins the build and isolates
its config, and the remote-execution plugin that sends its tools to the room's
executor. None of it is a fact about running an agent.

So the boundary is the point. ``tests/unit/test_harness_boundary.py`` holds it:
outside this package you import from HERE, never from a submodule, and this
file's export list is the ledger of everything that still crosses. The list is
a ratchet — adding to it goes red, and so does forgetting to delete a line you
paid off.
"""

from app.domain.agent.harness.claude_code.behaviour import declaration
from app.domain.agent.harness.claude_code.channel import ClaudeCodeChannel
from app.domain.agent.harness.claude_code.device_launch import (
    CLAUDE_MIN_VERSION,
    CLAUDE_PINNED_VERSION,
    DEVICE_TUNNEL_PROBE,
)
from app.domain.agent.harness.claude_code.remote_execution import (
    launch as executor_launch,
)
from app.domain.agent.harness.claude_code.remote_execution import (
    release as resident_release,
)
from app.domain.agent.harness.claude_code.remote_execution.private import (
    target as private_execution_target,
)
from app.domain.agent.harness.claude_code.runtime import ClaudeCodeRuntime

__all__ = [
    "CLAUDE_MIN_VERSION",
    "CLAUDE_PINNED_VERSION",
    "DEVICE_TUNNEL_PROBE",
    "ClaudeCodeChannel",
    "ClaudeCodeRuntime",
    "declaration",
    "executor_launch",
    "private_execution_target",
    "resident_release",
]
