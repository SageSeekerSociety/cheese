"""What a profile is: for one kind of session, which pieces its system prompt is
made of and when, which skills follow them, what opening sections it is told,
what it is told after every message, and which tools it holds.

Every condition on what a session reads lives in a profile (`profiles.py`) as
a predicate over :class:`Facts`; the pieces hold only text.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

#: The harness that hands its session a short name for the working directory
#: (`remote_execution.client` puts `$T` in the session's environment).
CLAUDE_CODE = "claude-code"


class Kind(StrEnum):
    """The kinds of session the platform runs."""

    main_line = "main_line"
    task = "task"
    thread = "thread"
    private = "private"
    document = "document"
    personal = "personal"
    dream_team = "dream_team"
    dream_private = "dream_private"
    situation = "situation"


@dataclass(frozen=True)
class Facts:
    """What a profile's conditions may ask about one session."""

    harness: str = CLAUDE_CODE
    #: The place has a living document.
    has_doc: bool = False
    #: The harness syncs the memory tree back to the platform (`keeps_memory`).
    keeps_memory: bool = False
    #: The teammate's name on the roster.
    name: str | None = None
    #: The teammate's expert role, as its owner wrote it.
    role: str | None = None
    #: The language the person who woke the session reads (`user.language`).
    language: str | None = None

    @property
    def working_directory(self) -> bool:
        """The session's commands carry `$T`, the working directory's short name."""
        return self.harness == CLAUDE_CODE


def always(_: Facts) -> bool:
    return True


#: Not pieces: places in a system prompt filled by what the caller passes —
#: the deployment's opening paragraph and the skill bodies.
BASE = "@base"
SKILLS = "@skills"
#: In a profile's skills: the skill bodies the teammate itself carries.
CARRIED = "@carried"


@dataclass(frozen=True)
class Include:
    """One piece of a system prompt, and when it is there."""

    #: A piece id, or `BASE` / `SKILLS`.
    piece: str
    when: Callable[[Facts], bool] = always
    #: Values the piece is there only when given (non-empty): `name`, `role`.
    needs: tuple[str, ...] = ()
    #: Slots filled with a skill's body: slot name -> skill name.
    skill_slots: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Profile:
    kind: Kind
    #: The system prompt's parts, in order, joined by a blank line.
    system: tuple[Include, ...]
    #: The tools the session holds: a tool (`cheese_task`, every verb) or one
    #: verb of it (`cheese_task.open`). Native tools are named as the harness
    #: names them (`Read`, `Bash`).
    tools: frozenset[str]
    #: Skill bodies at `SKILLS`, in order; `CARRIED` is the teammate's own.
    skills: tuple[str, ...] = ()
    #: The opening sections this kind is told (`opening.build_session_opening`).
    opening: frozenset[str] = frozenset()
    #: The piece said after every message (two languages), or None.
    notice: str | None = None
    #: Pieces said to the session on occasion (reminders), checked like the rest.
    reminders: tuple[str, ...] = field(default=())


class ProfileError(LookupError):
    """A piece a profile renders names a tool the profile does not hold."""

    def __init__(self, kind: Kind, piece: str, tool: str):
        super().__init__(
            f"{kind}: piece {piece} names {tool}, which {kind} does not hold"
        )
        self.kind = kind
        self.piece = piece
        self.tool = tool


def holds(tools: frozenset[str], key: str) -> bool:
    """Whether a tool set holds the tool or verb ``key``."""
    name, _, verb = key.partition(".")
    if key in tools or name in tools:
        return True
    return not verb and any(t.startswith(f"{name}.") for t in tools)
