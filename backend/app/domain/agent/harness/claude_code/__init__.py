"""The Claude Code adapter — 第一个 harness，和它的全部私有内部。

What is inside this package is how ONE harness happens to work, and none of it
is a fact about running an agent: a spool of hook files because Claude Code has
no event API, a message assembler because MessageDisplay fires per flush rather
than per message, a rendezvous socket pinned to one undocumented build, a
launch script that pre-accepts three first-run dialogs so the first prompt is
not eaten by a modal. Every one of those is a cost paid to drive a TUI written
for a person, and the next harness pays none of them.

So the boundary is the point. ``tests/unit/test_harness_boundary.py`` holds it:
outside this package you import from HERE, never from a submodule, and this
file's export list is the ledger of everything that still crosses. The list is
a ratchet — adding to it goes red, and so does forgetting to delete a line you
paid off.

What the list says today, honestly: the transports (tmux / device / cloud) are
``Channel`` implementations, so they import that one seam and the errors it
raises. That is the whole crossing — one runtime driven over any channel — and
what still shows up next to it (a ledger row taking ``build_screen_launch``,
or the hook env a transport still wires by hand) is Claude Code knowledge that
has not made it across the seam yet.
"""

from app.domain.agent.harness.claude_code.device_launch import (
    CLAUDE_MIN_VERSION,
    CLAUDE_PINNED_VERSION,
    DEVICE_ALIVE_PROBE,
    DEVICE_TUNNEL_PROBE,
    build_screen_launch,
)
from app.domain.agent.harness.claude_code.event_spool import append as append_event
from app.domain.agent.harness.claude_code.hook_events import (
    HookRouter,
    MessageAssembler,
    hook_router,
)
from app.domain.agent.harness.claude_code.hooks_substrate import (
    CHEESE_HOOK_SCRIPT,
    SESSION_TOKEN_TTL_S,
    ActivityTracker,
    Channel,
    ClaudeCodeRuntime,
    ScreenSetupError,
    SpoolBacklog,
    TopicSubscription,
    acknowledge_log,
    drop_device_subscriptions,
    drop_screen_subscriptions,
    drop_topic_subscriptions,
    expire_log,
    log_cursor,
    read_log,
)
from app.domain.agent.harness.claude_code.remote_execution.client import (
    REMOTE_CONTROLS,
    RemoteClient,
)
from app.domain.agent.harness.claude_code.remote_execution.launch import (
    script as build_executor_launch,
)
from app.domain.agent.harness.claude_code.remote_execution.private import (
    target as private_execution_target,
)
from app.domain.agent.harness.claude_code.session_launch import (
    HARNESS_ENV,
    build_session_launch,
    harness_of,
    hooks_settings,
)
from app.domain.agent.harness.claude_code.startup_cache import (
    build_startup_cache_prepare,
)
from app.domain.agent.harness.claude_code.warm_session import build_warm_session_prepare

__all__ = [
    "CHEESE_HOOK_SCRIPT",
    "REMOTE_CONTROLS",
    "RemoteClient",
    "build_executor_launch",
    "private_execution_target",
    "CLAUDE_MIN_VERSION",
    "CLAUDE_PINNED_VERSION",
    "DEVICE_ALIVE_PROBE",
    "DEVICE_TUNNEL_PROBE",
    "HARNESS_ENV",
    "SESSION_TOKEN_TTL_S",
    "ActivityTracker",
    "Channel",
    "ClaudeCodeRuntime",
    "HookRouter",
    "MessageAssembler",
    "ScreenSetupError",
    "SpoolBacklog",
    "TopicSubscription",
    "acknowledge_log",
    "append_event",
    "build_screen_launch",
    "build_session_launch",
    "build_startup_cache_prepare",
    "build_warm_session_prepare",
    "drop_device_subscriptions",
    "drop_screen_subscriptions",
    "drop_topic_subscriptions",
    "expire_log",
    "harness_of",
    "hooks_settings",
    "hook_router",
    "log_cursor",
    "read_log",
]
