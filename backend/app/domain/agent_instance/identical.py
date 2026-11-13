"""A project may not hold two active teammates that are exactly alike.

"Exactly alike" is what a member cannot tell apart in the team list: the same
name, the same saved configuration (model, thinking effort, skills, prompt,
execution target, compaction) and the same source — the platform's models, or
one particular team connection. A teammate on a team connection is named
after that connection (``own.naming``), so for those the source already decides
the name and only source and configuration are compared. Two teammates on one model stay allowed as
long as something a member can see differs, e.g. 芝士high and 芝士low on one
model at two effort levels. Retired teammates are not compared: they take no
work and cannot be picked.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UnprocessableEntityError
from app.core.sentences import say
from app.domain.agent_instance.configuration import AgentConfiguration
from app.domain.agent_instance.models import AgentInstance, OwnAgent

# Where a teammate's model comes from: None for the platform's own models,
# otherwise the team connection (a machine login or an account connection).
# The member it was added under is left out: a personal connection has only
# its owner, and a shared one names its teammates after itself alone.
Source = tuple[str | None, uuid.UUID | None] | None


def _shape(configuration: AgentConfiguration | dict[str, Any] | None) -> dict:
    config = (
        configuration
        if isinstance(configuration, AgentConfiguration)
        else AgentConfiguration.model_validate(configuration or {})
    )
    shape = config.model_dump()
    shape["skills"] = sorted(shape.get("skills") or [])
    return shape


def own_source(own: OwnAgent | None) -> Source:
    if own is None:
        return None
    return (own.login_id, own.account_connection_id)


async def refuse_identical(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    display_name: str | None,
    configuration: AgentConfiguration | dict[str, Any] | None,
    source: Source,
    exclude: uuid.UUID | None = None,
) -> None:
    """Refuse, naming the active teammate this one would duplicate.

    ``display_name`` None skips the name: the caller's name is derived from
    ``source`` and would be renumbered on save anyway.
    """
    name = display_name.strip() if display_name is not None else None
    shape = _shape(configuration)
    rows = (
        await db.execute(
            select(AgentInstance, OwnAgent)
            .outerjoin(OwnAgent, OwnAgent.instance_id == AgentInstance.id)
            .where(
                AgentInstance.project_id == project_id,
                AgentInstance.is_active.is_(True),
            )
        )
    ).all()
    for instance, own in rows:
        if instance.id == exclude:
            continue
        if (
            (name is None or (instance.display_name or "").strip() == name)
            and own_source(own) == source
            and _shape(instance.configuration) == shape
        ):
            raise UnprocessableEntityError(
                say(
                    "agentIdenticalExists",
                    name=instance.display_name or instance.handle,
                ),
                data={"handle": instance.handle},
            )
