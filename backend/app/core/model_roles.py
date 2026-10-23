"""Platform model roles: the gateway models the platform picks by itself.

A role is a job the platform runs on a model nobody chose per request: the
default a project without its own default runs on, the speech-to-text behind
voice input, the model that names a topic. Each used to be a field in
``Settings``, so repointing one took a backend redeploy, and the admin page
could not tell that deleting a model would take voice input with it.

Now an administrator points a role at a gateway model on the admin models page
(``app.domain.agent.gateway_models.roles``), the choice is stored in
``platform_model_roles`` and audited, and every reader asks this module. A role
nobody has pointed falls back to its ``Settings`` field, so a deployment reads
exactly what it read before until someone changes a role.

Read it synchronously (``get``); the stored choices are kept warm by
``refresh`` on a timer and after every write in the writing process (the same
split as ``app.domain.agent.gateway_catalog``), so the readers on the request
path neither make a database call nor have to become async. Another process
picks a change up within ``Settings.platform_model_roles_refresh_interval_s``.

A project's own settings still win where a project has them: ``default_model``
over ``agent_model``, ``default_subagent_model`` over ``subagent_model``
(``model_catalog.subagent_default``).

Infrastructure, not a domain: every domain that calls a model reads it, so it
needs nothing but ``Settings``.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from app.core.config import settings


@dataclass(frozen=True)
class Role:
    """``fallback`` is the ``Settings`` field read while no choice is stored
    (None: no fallback, the role is unset). ``offered``: the model must be one
    the picker offers, because the role is what a project is offered by
    default. ``keys``: the aliases of the gateway virtual keys minted for this
    job (``service_keys.KeySpec.alias``), which allow only this model."""

    fallback: str | None
    offered: bool = False
    keys: tuple[str, ...] = ()


# Order is the order the admin page lists them in.
ROLES: dict[str, Role] = {
    "agent_model": Role("agent_model", offered=True),
    "subagent_model": Role(None, offered=True),
    "assistant_model": Role("assistant_model", keys=("assistant-gate",)),
    "docs_assistant_model": Role("docs_assistant_model", keys=("docs-assistant",)),
    # Both gates that name things off a message: a task's title, and a
    # bookmark's (``app.domain.chapter.naming``). They are two keys so neither
    # spends the other's rate limit, but one role, so moving the role moves
    # both — a key left behind would keep calling the old model.
    "topic_naming_model": Role(
        "topic_naming_model", keys=("topic-naming", "bookmark-naming")
    ),
    "environment_diagnosis_model": Role(
        "environment_diagnosis_model", keys=("environment-diagnosis",)
    ),
    "voice_asr_model": Role("voice_asr_model", keys=("voice-asr",)),
    "voice_polish_model": Role("voice_polish_model", keys=("voice-polish",)),
    "memory_migration_model": Role(
        "memory_migration_model", keys=("memory-migration",)
    ),
}


@dataclass
class _State:
    chosen: dict[str, str]


_state = _State(chosen={})


def fallback(role: str) -> str | None:
    """What ``role`` reads while nobody has pointed it: its ``Settings`` field."""
    field = ROLES[role].fallback
    value = getattr(settings, field) if field else None
    return value if isinstance(value, str) and value else None


def get(role: str) -> str | None:
    """The model ``role`` runs on now. Never a database call."""
    return _state.chosen.get(role) or fallback(role)


def model(role: str) -> str:
    """``get`` for a role that always has a value (every role but
    ``subagent_model`` falls back to a ``Settings`` field with a default)."""
    value = get(role)
    if value is None:
        # A caller asked for a role with no fallback: a bug, never shown.
        raise LookupError(role)
    return value


def chosen(role: str) -> bool:
    """Has an administrator pointed ``role`` (rather than it reading its field)."""
    return role in _state.chosen


def pointing_at(name: str) -> list[str]:
    """The roles that run on the model ``name`` now."""
    return [role for role in ROLES if get(role) == name]


def install(chosen: Mapping[str, str]) -> None:
    """Replace the stored choices this process knows (``refresh`` reads them)."""
    _state.chosen = {role: m for role, m in chosen.items() if role in ROLES and m}


def reset() -> None:
    """Forget the stored choices — the seam a test uses."""
    _state.chosen = {}
