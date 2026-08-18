"""What an agent type may be configured with — the one place that knows.

A picker is a promise: every value it offers takes effect. Half the fields on
``agent_types`` are stored and read by nobody (``effort``, ``harness``,
``skills``, ``mcp_servers`` have no consumer on the run path), so a dropdown
over them would be a promise the platform cannot keep — the user picks, the
agent runs exactly as before, and nothing in the product says why.

So a field is described here as either *choosable* (with its choices) or
*unavailable* **with a named reason**. Unavailable means the editor renders
NOTHING for it — not an input, and not a note explaining the absence either;
both leave a place on screen for a feature that does not exist, and a person
reading the note starts waiting for it. The reason is for whoever wires the
field up later, not for the user. Wiring it means moving the field from one
state to the other HERE, and the editor follows without being touched.

(Shape and rule are Buzz's — `desktop/src/features/agents/AGENTS.md`: "Field
absence has a named reason, not a boolean", where the reasons live on the
render model and never reach the screen.)
"""

from dataclasses import asdict, dataclass, field

from app.domain.agent.market import (
    subscription_model_default,
    subscription_model_listings,
)


@dataclass(frozen=True)
class Choice:
    id: str
    label: str
    description: str = ""
    default: bool = False


@dataclass(frozen=True)
class FieldOptions:
    """One configurable field, in exactly one of two states.

    ``choosable`` — ``choices`` is the whole set of values that take effect.
    ``unavailable`` — ``reason`` names why, and the UI says so rather than
    offering an input that would silently do nothing.
    """

    state: str
    choices: list[Choice] = field(default_factory=list)
    reason: str = ""
    note: str = ""


# Why a field is not offered. One constant per distinct cause, so the UI copy
# and any future fix both point at the same thing.
NO_CONSUMER = "noConsumerOnRunPath"
NO_REGISTRY = "noRegistryYet"


def _model_field() -> FieldOptions:
    """The models a turn can actually be run with.

    Same list the project-level model picker uses, because it is the same
    switch one level down: an agent type that names a model overrides the
    project's choice for the topics that agent works in.
    """
    default = subscription_model_default()
    return FieldOptions(
        state="choosable",
        choices=[
            Choice(
                id=listing.id,
                label=listing.label,
                description=listing.description,
                default=listing.id == default,
            )
            for listing in subscription_model_listings()
        ],
    )


def agent_type_options() -> dict:
    """Every configurable field of an agent type, keyed by field name."""
    fields = {
        "model": _model_field(),
        # Claude Code takes reasoning effort from its own runtime, not from an
        # env var we set — so a value stored here would never reach the turn.
        "effort": FieldOptions(
            state="unavailable",
            reason=NO_CONSUMER,
            note="模型自己决定思考深度，平台设了也不会生效",
        ),
        # One harness ships today and every session launches it; a picker with
        # one entry is a choice nobody has.
        "harness": FieldOptions(
            state="unavailable",
            reason=NO_CONSUMER,
            note="目前只有一种运行方式，无从选起",
        ),
        # The skill library is composed per scenario by the platform. It is not
        # a menu of capabilities to hand an agent, and nothing reads the list
        # stored on a type.
        "skills": FieldOptions(
            state="unavailable",
            reason=NO_CONSUMER,
            note="技能目前由平台按场景自动搭配",
        ),
        "mcp_servers": FieldOptions(
            state="unavailable",
            reason=NO_REGISTRY,
            note="还没有可选的 MCP 服务目录",
        ),
    }
    return {name: asdict(opts) for name, opts in fields.items()}
