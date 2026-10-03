"""What the session core is asked with and what it reads back.

A session is named by ``SessionRef``: which harness, and where on the session
host it keeps its conversation. It is started by ``SessionSpec`` (what the
process is) and ``Access`` (what it acts with and on); a ``Prompt`` is one thing
said to it. Reading it from a cursor yields ``Read`` items, each carrying the
cursor to read on from.

Nothing here knows a room, a document or a person: those are the assemblies'
(``agent.document``, ``agent.personal``).
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field

from app.domain.agent.service import AgentEvent

#: A position in a session's journal, as the runner names its entries.
Cursor = str


class SessionError(RuntimeError):
    """The session could not be started or reached; the message says why."""


class HostFull(SessionError):
    """The session host has no memory for one more session right now."""


@dataclass(frozen=True)
class SessionRef:
    """One session: its harness, and where it lives on the session host.

    ``home`` is a path under the host's ``~/.cheese``. Sessions whose homes
    share a parent directory are counted together (``Footprint.group_limit``);
    the core does not otherwise read it."""

    harness: str
    home: str

    @property
    def state(self) -> str:
        """Its state directory, as the connector expands it."""
        return f"$HOME/.cheese/{self.home}"


@dataclass(frozen=True)
class ModelSettings:
    """How the model is called, beyond which model it is. The defaults are the
    provider's own."""

    thinking: bool = True
    #: The longest answer, in tokens.
    max_tokens: int | None = None
    #: The context the conversation may grow to.
    context_tokens: int | None = None
    #: Compaction: how much of the context is kept free, and how much of the
    #: latest conversation is kept verbatim when the older part is summarised.
    reserve_tokens: int | None = None
    keep_tokens: int | None = None


@dataclass(frozen=True)
class Footprint:
    """What one session may take of the session host."""

    #: Memory, the harness and its runner together.
    memory_mb: int
    #: How many sessions under the same parent directory may run at once;
    #: starting one more lets the least recently used idle one go.
    group_limit: int


@dataclass(frozen=True)
class SessionSpec:
    """What a session's process is started as.

    A running session is started again when its model changes; the launch
    itself replaces a runner whose build, arguments, tools or execution target
    differ once it is idle. The system prompt and the resume token take effect
    on a cold start only."""

    system_prompt: str
    model: str
    #: The tool table's tools it has (`sandbox/cheese`), by name.
    tools: tuple[str, ...]
    footprint: Footprint
    #: How long it may sit idle before it exits.
    idle_exit_s: float
    #: How long its runner may be out of reach mid-work before the work is
    #: given up on.
    gone_after_s: float
    model_settings: ModelSettings = ModelSettings()
    #: The conversation it resumes.
    resume_token: str | None = None
    #: What its tools read of where they act (the project, the room).
    env: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Access:
    """What a session acts with and on.

    ``credential`` is what it calls the model with, and its tools when a prompt
    brings none of its own. ``target`` is the machine its own file tools work
    on, when it has one (`remote_execution`)."""

    credential: str
    target: dict | None = None


@dataclass(frozen=True)
class Prompt:
    """One thing said to a session."""

    #: Recorded by the caller before it is sent; the session's answer to it is
    #: filed under the work it is sent with.
    id: uuid.UUID
    text: str
    #: What the session's tools act with while answering it, when that is not
    #: the session's own credential: one minted for this prompt.
    acting: str | None = None
    #: Said ahead of ``text`` to a session nobody has spoken to yet: what was
    #: said before the session existed.
    preface: str = ""


@dataclass(frozen=True)
class Writing:
    """What the session is in the middle of writing, as it stands now."""

    blocks: tuple[dict, ...]


@dataclass(frozen=True)
class Ended:
    """The session is gone: its runner exited, or stayed out of reach for
    longer than ``SessionSpec.gone_after_s``."""

    reason: str


@dataclass(frozen=True)
class Read:
    """One thing read from a session, and the cursor to read on from."""

    cursor: Cursor | None
    #: The work it belongs to, when the session says.
    work_id: str | None
    event: AgentEvent | Writing | Ended


@dataclass(frozen=True)
class SessionStatus:
    """A running session, as its runner says it is now."""

    working: bool
    model: str
