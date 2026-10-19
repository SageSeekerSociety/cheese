"""A session's execution environment: where its tools run (#2320).

Every agent in every conversation has its own environment, kept apart from the
session row (``AgentEnvironmentRow``) so that a change of harness, which makes
a new session row, keeps it. A room, a task and a project hold only the
default a new agent gets there.

An environment is one of two things:

- a sandbox, ``{"kind": "sandbox", "spec": ...}``. The spec names the kind of
  machine the sandbox needs, and every spec is a peer of the others, as resource
  classes are: ``standard`` and ``vm`` are the platform's machines, and
  ``team_devices`` is the team's enrolled computers. The platform
  places the sandbox on a device that offers its spec; a sandbox never names a
  device, and nobody is shown which one it is on.
- a self-hosted machine, ``{"kind": "self_hosted", "device_id": ...}``: the one
  case where a session must use one particular enrolled computer. It runs
  there without isolation, so only the device's team administrators may choose it.
"""

import uuid
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from pydantic import ValidationError as SchemaError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import UnprocessableEntityError
from app.core.sentences import say
from app.domain.agent.models import AgentTurn
from app.domain.conversation.services import of_room
from app.domain.device.wiring import sql_device_service

#: The platform's machines, the default for a new agent.
STANDARD = "standard"
#: A whole virtual machine of the platform's for the sandbox: Docker, KVM,
#: kernel modules, root.
VM = "vm"
#: The devices enrolled into the project's team.
TEAM_DEVICES = "team_devices"


async def room_is_busy(session: AsyncSession, room_id: uuid.UUID) -> bool:
    """An open turn anywhere in the room keeps its environment in use."""
    active = await session.scalar(
        select(AgentTurn.id)
        .where(
            of_room(AgentTurn.conversation_id, room_id),
            AgentTurn.stopped_at.is_(None),
        )
        .limit(1)
    )
    return active is not None


class Sandbox(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["sandbox"] = "sandbox"
    spec: str = Field(default=STANDARD, min_length=1, max_length=64)


class SelfHosted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["self_hosted"]
    device_id: str = Field(min_length=1, max_length=64)


Environment = Annotated[Sandbox | SelfHosted, Field(discriminator="kind")]
_ENVIRONMENT: TypeAdapter[Sandbox | SelfHosted] = TypeAdapter(Environment)


def parse(raw: object) -> Sandbox | SelfHosted:
    """``raw`` as an environment, or a refusal a person can read."""
    try:
        return _ENVIRONMENT.validate_python(raw)
    except SchemaError as exc:
        raise UnprocessableEntityError(say("environmentInvalid")) from exc


def stored(raw: object) -> Sandbox | SelfHosted | None:
    """An environment read back from storage; None when nothing is stored."""
    if not raw:
        return None
    return _ENVIRONMENT.validate_python(raw)


def dump(environment: Sandbox | SelfHosted | None) -> dict | None:
    return None if environment is None else environment.model_dump()


@dataclass(frozen=True)
class Spec:
    """What a sandbox spec is: who supplies its devices, whether running on
    them costs the project credits, how long an idle sandbox is kept, and how
    long a command still running for the session keeps it up."""

    name: str
    #: The platform's machines (``standard``, ``vm``), as against a team's.
    platform: bool
    idle_s: float
    #: How long a command the executor still runs for the session keeps an
    #: idle sandbox up, counted from the session's last activity. A whole VM's
    #: idle release is longer than a sandbox's, so its cap is longer too.
    background_cap_s: float

    @property
    def paid(self) -> bool:
        return self.platform


def spec_of(name: str) -> Spec | None:
    """The spec ``name`` names, or None for one that does not exist."""
    if name == STANDARD:
        return Spec(
            STANDARD,
            platform=True,
            idle_s=settings.cloud_sandbox_idle_stop_s,
            background_cap_s=settings.cloud_sandbox_background_cap_s,
        )
    if name == VM:
        return Spec(
            VM,
            platform=True,
            idle_s=settings.cloud_vm_idle_release_s,
            background_cap_s=settings.cloud_vm_background_cap_s,
        )
    if name == TEAM_DEVICES:
        return Spec(
            name,
            platform=False,
            idle_s=settings.cloud_sandbox_idle_stop_s,
            background_cap_s=settings.cloud_sandbox_background_cap_s,
        )
    return None


def default_environment() -> Sandbox:
    """The environment of an agent nothing chose one for."""
    return Sandbox(spec=STANDARD)


def project_default(project_settings: dict | None) -> Sandbox | SelfHosted | None:
    """The default a project set for its new agents, if it set one."""
    return stored((project_settings or {}).get("execution_environment"))


async def works_tasks_of(
    session: AsyncSession, device_id: str, project, owner_handle: str | None
) -> bool:
    """Whether a device may work a task owned by ``owner_handle``.

    A device belongs to the project's team and works any member's tasks."""
    devices = sql_device_service(session)
    device = await devices.get_device(device_id)
    if device is None:
        return False
    return device.team_id == project.team_id
