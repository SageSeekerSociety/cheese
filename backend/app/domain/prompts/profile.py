"""What a profile is: for one kind of session, which pieces its system prompt is
made of and when, which skills follow them, what opening sections it is told,
what it is told after every message, and which tools it holds.

Every condition on what a session reads lives in a profile (`profiles.py`) as
a predicate over :class:`Facts`; the pieces hold only text.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum


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

    #: The session's commands carry `$T`, the working directory's short name
    #: (only the harness that puts it in their environment).
    working_directory: bool = False
    #: The place has a living document.
    has_doc: bool = False
    #: The harness this turn runs syncs the memory tree back to the platform
    #: (`session_host.host.keeps_memory`).
    memory_synced: bool = False
    #: The teammate's name on the roster.
    name: str | None = None
    #: The teammate's expert role, as its owner wrote it.
    role: str | None = None
    #: The language the person who woke the session reads (`user.language`).
    language: str | None = None


def always(_: Facts) -> bool:
    """总在"""
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
    #: Slots this kind fills with a fixed value.
    slots: dict[str, str] = field(default_factory=dict)


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
    #: The thinking effort a session of this kind runs at when its teammate set
    #: none. Fixed for the session's life: on several providers each effort has
    #: a cache of its own, so changing it mid-session reads the whole history
    #: again.
    effort: str | None = None
    #: The tools that answer a person who wrote to this session, and whether
    #: every other tool waits until one of them is called (the runner's reply
    #: rule, `driven.runner.REPLY_TOOLS_ENV`). The main line answers after it
    #: has opened or told a task, so it holds nothing back.
    reply_tools: tuple[str, ...] = ("chat_send", "cheese_ask")
    reply_held: bool = True
    #: A turn the platform started (a task's report, its turn among teammates
    #: named together) is held to a visible act at its end, as a person's is.
    answers_the_platform: bool = False
    #: The session is offered only `tools`, of the platform's and of the
    #: harness's own, and the rest are refused; otherwise it is offered all.
    narrowed: bool = False
    #: Where the session may write files: None anywhere it works, "memory"
    #: only its memory tree.
    writes: str | None = None
    #: Which rulebook of this kind a session is started under. Raised by hand
    #: when the kind's role changes so that its earlier turns would contradict
    #: the new rules: a session started under another generation is not
    #: resumed, and the place starts a new one (`agent_sessions.generation`).
    #: A session recorded before generations were counted is generation 0.
    generation: int = 0


class ProfileError(LookupError):
    """A piece a profile renders names a tool the profile does not hold."""

    def __init__(self, kind: Kind, piece: str, tool: str):
        # i18n-exempt: a profile names a tool it does not hold, for a developer
        super().__init__(f"{kind}: piece {piece} names {tool}, which it does not hold")
        self.kind = kind
        self.piece = piece
        self.tool = tool


def holds(tools: frozenset[str], key: str) -> bool:
    """Whether a tool set holds the tool or verb ``key``."""
    name, _, verb = key.partition(".")
    if key in tools or name in tools:
        return True
    return not verb and any(t.startswith(f"{name}.") for t in tools)
