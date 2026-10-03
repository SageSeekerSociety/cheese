"""A document comment thread's 芝士 on the session host: one pi session per
thread.

Someone names the room's agent in a comment on the room's living document, and
the thread's own session answers: it reads what it is handed, changes the
document with the tools it is given, and its answer becomes the agent's reply in
the thread. The room's conversation is not interrupted for it.

When the room's machine is there, the session reads the room's work on it
(``machine``, `machine/reading.py`): pi's own read, ls, find and grep, with
their hands on the room's checkout, and nothing that writes or runs a command.
It never takes a machine of its own; a room with none in hand gets an answer
from the document and the platform alone.

One thread is one pi session, under the thread's id, in a state directory under
its project's (`state_dir`), so a project's sessions are siblings: starting one
more than ``SESSIONS_PER_PROJECT`` lets the project's least recently used idle
one go (`host._make_room`). A session sits idle for ``IDLE_EXIT_S`` and exits;
the thread's next question starts it again on the same conversation. Each runs
under ``MEMORY_MB`` (`host._capped`).

It runs on the room's agent's model, called with a credential naming the room
and the agent, so what it spends is the project's like any call of that agent
(`llm_proxy`). Everything after the launch is `handless.py`.
"""

import uuid
from dataclasses import dataclass

from app.domain.agent.harness.pi.launch import (
    HostLaunch,
    arguments,
    extension,
    on_host,
)

#: How long a thread's session may sit idle before it exits.
IDLE_EXIT_S = 60.0
#: How many of one project's sessions may be kept running at once.
SESSIONS_PER_PROJECT = 4
#: The memory each may use, pi and runner together.
MEMORY_MB = 512


@dataclass(frozen=True)
class Launch:
    """What a thread's session is started with."""

    project_id: uuid.UUID
    #: The room whose document it answers in: where its tools act.
    room_id: uuid.UUID
    thread_id: uuid.UUID
    system_prompt: str
    #: The table tools it has (`sandbox/cheese`), by name.
    tools: tuple[str, ...]
    #: The room's credential for the agent, naming this thread.
    token: str
    model: str
    #: The room's machine to read, when the room holds one that is there.
    machine: dict | None = None

    @property
    def key(self) -> uuid.UUID:
        return self.thread_id

    @property
    def state(self) -> str:
        return state_dir(self.project_id, self.thread_id)

    @property
    def memory_mb(self) -> int:
        return MEMORY_MB

    def on_host(self, api: str) -> HostLaunch:
        return on_host(
            state=self.state,
            config=configuration(self),
            api_base=api,
            model=self.model,
            env={
                "CHEESE_API": api,
                "CHEESE_TOKEN": self.token,
                "CHEESE_PROJECT": str(self.project_id),
                "CHEESE_TOPIC": str(self.room_id),
            },
            host={
                "group_limit": SESSIONS_PER_PROJECT,
                "memory_max": f"{MEMORY_MB}M",
            },
        )


def state_dir(project_id: uuid.UUID, thread_id: uuid.UUID) -> str:
    """Where a thread's session lives on the session host, as the connector
    expands it; one directory per project, so its sessions are siblings."""
    return f"$HOME/.cheese/docs/{project_id}/{thread_id}"


def configuration(launch: Launch) -> dict:
    return {
        "opening": {
            "system_prompt": launch.system_prompt,
            "resume_token": str(launch.thread_id),
            "model": launch.model,
        },
        "args": arguments(launch.model),
        "execution_target": launch.machine,
        "skills": {},
        "extension": extension(),
        "notice": "",
        "idle_exit_s": IDLE_EXIT_S,
        "tools": {"names": list(launch.tools)},
    }
