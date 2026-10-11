"""A session's execution environment: where its tools run (#2320).

Every agent in every conversation has its own environment, kept apart from the
session row (``AgentEnvironmentRow``) so that a change of harness, which makes
a new session row, keeps it. A room, a task and a project hold only the
default a new agent gets there.

An environment is one of two things:

- a sandbox, ``{"kind": "sandbox", "spec": ...}``. The spec names the kind of
  machine the sandbox needs, and every spec is a peer of the others, as resource
  classes are: ``standard`` and ``vm`` are the platform's machines, and
  ``devices_of:<user id>`` is one person's own enrolled computers. The platform
  places the sandbox on a device that offers its spec; a sandbox never names a
  device, and nobody is shown which one it is on.
- a self-hosted machine, ``{"kind": "self_hosted", "device_id": ...}``: the one
  case where a session must use one particular enrolled computer. It runs
  there without isolation, so only that computer's owner may choose it.
"""

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from pydantic import ValidationError as SchemaError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import UnprocessableEntityError
from app.core.sentences import say
from app.domain.device.wiring import sql_device_service
from app.domain.user.models import User as UserRow

#: The platform's machines, the default for a new agent.
STANDARD = "standard"
#: A whole virtual machine of the platform's for the sandbox: Docker, KVM,
#: kernel modules, root.
VM = "vm"
#: One person's own enrolled computers: ``devices_of:<user id>``.
DEVICES_OF = "devices_of:"


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
    them costs the project credits, and how long an idle sandbox is kept."""

    name: str
    #: The platform's machines (``standard``, ``vm``), as against a person's.
    platform: bool
    idle_s: float
    #: The person whose computers ``devices_of:<id>`` places on.
    owner_user_id: int | None = None

    @property
    def paid(self) -> bool:
        return self.platform


def spec_of(name: str) -> Spec | None:
    """The spec ``name`` names, or None for one that does not exist."""
    if name == STANDARD:
        return Spec(STANDARD, platform=True, idle_s=settings.cloud_sandbox_idle_stop_s)
    if name == VM:
        return Spec(VM, platform=True, idle_s=settings.cloud_vm_idle_release_s)
    if name.startswith(DEVICES_OF):
        owner = name.removeprefix(DEVICES_OF)
        if owner.isdigit():
            return Spec(
                name,
                platform=False,
                idle_s=settings.cloud_sandbox_idle_stop_s,
                owner_user_id=int(owner),
            )
    return None


def devices_of(user_id: int) -> str:
    return f"{DEVICES_OF}{user_id}"


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

    A device shared with the project's team is the team's, and works anyone's
    tasks. Any other device in the project is a person's own computer, and
    works only its owner's tasks."""
    devices = sql_device_service(session)
    device = await devices.get_device(device_id)
    if device is None:
        return False
    if project.team_id in await devices.list_teams(device_id):
        return True
    owner = (
        await session.scalar(select(UserRow).where(UserRow.username == owner_handle))
        if owner_handle
        else None
    )
    return owner is not None and owner.id == device.owner_user_id
