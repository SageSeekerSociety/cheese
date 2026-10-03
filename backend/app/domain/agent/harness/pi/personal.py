"""A person's 芝士 on the session host: one pi session per conversation, with no hands.

It is the same harness a room runs — the pinned pi, its runner, the launch that
leaves the runner going (`launch.py`, `host.configure`), the read that waits at
the runner for news and carries what pi is writing (`driven.runner`) — started
for a conversation instead of a room's seat. What a room needs and a person's
芝士 does not is absent rather than idle: no project, no machine, no topic, no
lease, no screen, no room log. The session is told to use only the tools it is
given, which the platform runs as the person (`runner._platform_tool`).

One conversation is one pi session, under the conversation's id, in a state
directory of its own under the person's (`state_dir`). A session sits idle for
``IDLE_EXIT_S`` and then exits; its conversation stays on disk, and the next
question starts it again on it. A person keeps at most ``SESSIONS_PER_PERSON``
running: starting one more lets their least recently used one go
(`host._make_room`). Each runs under ``MEMORY_MB`` (`host._capped`).

Everything after the launch is `handless.py`, shared with a document thread's
session.
"""

import json
import uuid
from dataclasses import dataclass

from app.domain.agent.harness.pi.launch import (
    HostLaunch,
    arguments,
    extension,
    on_host,
    provider,
)

#: How long a person's session may sit idle before it exits.
IDLE_EXIT_S = 180.0
#: How many of one person's sessions may run at once.
SESSIONS_PER_PERSON = 2
#: The memory each may use, pi and runner together.
MEMORY_MB = 512
#: The longest answer, in tokens; the model's other limits follow from it.
MAX_TOKENS = 1500
#: The context the session may grow to, and how much of it compaction keeps
#: free: pi summarises the older part of the conversation once a question's
#: context passes ``CONTEXT_TOKENS - RESERVE_TOKENS``, keeping the latest
#: ``KEEP_TOKENS`` verbatim. What a question costs stays bounded however long
#: the conversation runs.
CONTEXT_TOKENS = 32_000
RESERVE_TOKENS = 16_000
KEEP_TOKENS = 6_000


@dataclass(frozen=True)
class Launch:
    """What a person's session is started with."""

    user_id: int
    conversation_id: uuid.UUID
    system_prompt: str
    #: The table tools it has (`sandbox/cheese`), by name.
    tools: tuple[str, ...]
    #: Its personal credential (`sandbox_auth.mint_personal_credential`).
    token: str
    model: str

    @property
    def key(self) -> uuid.UUID:
        return self.conversation_id

    @property
    def state(self) -> str:
        return state_dir(self.user_id, self.conversation_id)

    @property
    def memory_mb(self) -> int:
        return MEMORY_MB

    def on_host(self, api: str) -> HostLaunch:
        return on_host(
            state=self.state,
            config=configuration(self),
            api_base=api,
            model=self.model,
            env={"CHEESE_API": api, "CHEESE_TOKEN": self.token},
            models=models(api, self.model),
            host={
                "group_limit": SESSIONS_PER_PERSON,
                "memory_max": f"{MEMORY_MB}M",
                "settings": pi_settings(),
            },
        )


def state_dir(user_id: int, conversation_id: uuid.UUID) -> str:
    """Where a conversation's session lives on the session host, as the
    connector expands it; one directory per person, so their sessions are
    siblings (`host._make_room`)."""
    return f"$HOME/.cheese/personal/{user_id}/{conversation_id}"


def models(api: str, model: str) -> str:
    """The provider file: the room's, with thinking off and the answer bounded.

    The gateway's deepseek models think unless told not to, and a model that
    thinks spends its output on it; pi says so to them only for a model it
    knows can reason (``reasoning``) and in their dialect (``thinkingFormat``),
    with ``--thinking off`` on the command line choosing the off.
    """
    shape = json.loads(provider(api, model))
    cheese = shape["providers"]["cheese"]
    cheese["compat"]["thinkingFormat"] = "deepseek"
    cheese["models"] = [
        {
            "id": model,
            "reasoning": True,
            "maxTokens": MAX_TOKENS,
            "contextWindow": CONTEXT_TOKENS,
        }
    ]
    return json.dumps(shape, ensure_ascii=False, indent=2)


def pi_settings() -> str:
    return json.dumps(
        {
            "compaction": {
                "reserveTokens": RESERVE_TOKENS,
                "keepRecentTokens": KEEP_TOKENS,
            }
        }
    )


def configuration(launch: Launch) -> dict:
    return {
        "opening": {
            "system_prompt": launch.system_prompt,
            "resume_token": str(launch.conversation_id),
            "model": launch.model,
        },
        "args": [*arguments(launch.model), "--thinking", "off"],
        "execution_target": None,
        "skills": {},
        "extension": extension(),
        "notice": "",
        "idle_exit_s": IDLE_EXIT_S,
        "tools": {"names": list(launch.tools)},
    }
