"""What a Claude Code session is started with: its settings.

The settings file is read ONCE, at launch: a session that is merely reused keeps
what it started with. It is written on every launch anyway, because the write is
for the NEXT fresh session. Writing it is ``device_launch``'s half — this module
only says what goes in.

``ClaudeLaunch``, the plan a channel is handed, lives in ``device_launch`` beside
``on_machine``: ``on`` can only be answered there, and a plan that imported this
module while this module's only reader imported the plan would be a cycle
(``.importlinter``, C3).
"""

import json
import shlex

from app.domain.agent import gateway_catalog
from app.domain.agent.harness.claude_code.cli import DISALLOWED_TOOLS
from app.domain.agent.harness.prompt import SUBAGENT_RULES

#: The window Claude Code assumes for a model id it does not recognise.
_ASSUMED_WINDOW = 200_000
#: The range Claude Code accepts for an auto-compact window.
_COMPACT_RANGE = (100_000, 1_000_000)


def compact_windows() -> dict[str, dict[str, int]]:
    """Claude Code's ``modelSettings``: where each gateway model compacts.

    Per model, because one session runs several: its own model, the subagent
    default, and the models offered as subagent types. A model compacts where
    its provider recommends (``GatewayModel.compact_window``), else at the
    window the gateway declares, else at the 200K Claude Code would assume
    anyway. Claude Code caps each at the window it believes the session's
    model has (``gateway_usage.window_env``), and a subagent on a model with a
    smaller window than that compacts before it overflows its own. A
    ``claude-`` id is left out: Claude Code knows its window itself."""
    low, high = _COMPACT_RANGE
    return {
        m.id: {
            "autoCompactWindow": min(
                high,
                max(low, m.compact_window or m.context_window or _ASSUMED_WINDOW),
            )
        }
        for m in gateway_catalog.snapshot()
        if not m.id.lower().startswith("claude-")
    }


def session_settings() -> dict:
    """Base Claude settings for one seat's session.

    No hook here observes the session: the runner reads what it does from its
    own stdout. The hook left is one that acts — the step-title and
    shared-checkout rules put in front of every agent the session starts — and
    the remote-execution client adds the rest of its own (``client.prepare``).
    """
    # An agent the session starts — the Agent tool's, a workflow's — gets none
    # of the session's system prompt, but its every step is a line on the room's
    # 施工现场 all the same, and it commits, pulls and starts servers beside
    # other tasks. SubagentStart fires for both, and what it prints as
    # additionalContext opens that agent's conversation.
    subagent_rules = [
        {
            "hooks": [
                {
                    "type": "command",
                    "command": shlex.join(
                        [
                            "printf",
                            "%s",
                            json.dumps(
                                {
                                    "hookSpecificOutput": {
                                        "hookEventName": "SubagentStart",
                                        "additionalContext": SUBAGENT_RULES,
                                    }
                                },
                                ensure_ascii=False,
                            ),
                        ]
                    ),
                }
            ]
        }
    ]
    return {
        "skipDangerousModePermissionPrompt": True,
        # Stream liveness is separate from WebFetch's response-error handling.
        "env": {
            "CLAUDE_ENABLE_STREAM_WATCHDOG": "1",
            # The watchdog aborts a stream that has produced no BYTES for its
            # idle window, and left alone that window is SHORTER than this
            # deployment's own silence handling: the CLI uses 180 s whenever it
            # believes it is on the first-party API, which is exactly what an
            # unset `ANTHROPIC_BASE_URL` means (provider_env.py leaves it unset
            # so the metering proxy stays transparent). Nothing on the path
            # writes a keepalive byte either — LiteLLM holds the first chunk
            # until TTFT and mitmproxy stays silent while the provider thinks —
            # so a long thinking window is indistinguishable from a dead
            # connection, and the CLI kills the turn mid-stream before the
            # platform's own gates ever look at it.
            #
            # Setting this variable at all is what leaves that 180 s branch (the
            # CLI floors it at 300 s and never honours anything lower), so the
            # number here is a ceiling for the CLI, not a second opinion on how
            # long a turn may run.
            "CLAUDE_STREAM_IDLE_TIMEOUT_MS": "900000",
            # A stream that breaks mid-response — the watchdog above aborting
            # it, an error event, a dropped connection — is otherwise sent
            # again WITHOUT streaming, and a model route that only streams (a
            # ChatGPT-subscription deployment behind the gateway) refuses that
            # with 400 "Stream must be set to true", ending the turn. Set, the
            # stream's own error goes to the CLI's retry layer instead.
            "CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK": "1",
        },
        # Previews belong in Cheese, not on claude.ai via the Artifact tool.
        "enableArtifact": False,
        # The same refusal the argv carries (`cli.LAUNCH_ARGS`), stated where a
        # settings reader looks for it.
        "permissions": {"deny": list(DISALLOWED_TOOLS)},
        # Set before the first turn: changing this later cannot remove a URL
        # already present in the conversation's model-visible history.
        # Empty commit/pr: no "Co-Authored-By: Claude" trailer on the commits a
        # teammate makes, and no "Generated with Claude Code" line under its PRs.
        # The work is the teammate's, credited through the platform's own
        # identities (`domain/repository/identity.py`), not to the CLI.
        "attribution": {"commit": "", "pr": "", "sessionUrl": False},
        "hooks": {
            "SubagentStart": subagent_rules,
        },
        "modelSettings": compact_windows(),
    }
