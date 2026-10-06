"""The project default and room-local resource choices."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import String, cast, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ValidationError
from app.core.sentences import say
from app.domain.agent.market import (
    COMPUTE_DEVICE,
    COMPUTE_TIERS,
    cloud_provisionable,
    cloud_vm_provisionable,
    compute_default_name,
)
from app.domain.device.supply import default_visibility
from app.domain.device.wiring import sql_device_service
from app.domain.policy import gate
from app.domain.user.models import User as UserRow


class ComputeChoice(BaseModel):
    """Which machine a room works on: a cloud sandbox, a whole cloud VM, or a
    self-hosted device.

    Cloud is either a sandbox on a platform host, or with ``whole_machine`` a
    whole virtual machine for each session, for work a sandbox cannot do
    (Docker, KVM, kernel modules, root). Neither has a spec to choose: every
    sandbox gets the same fixed share of a host, and every VM the deployment's
    one VM size. ``name`` is only ever a device's own name, a proper
    noun that reads the same in every language. A choice the platform describes
    — the cloud, and 「any online device」 — carries no name: it is identified
    by its fields, and each reader's screen renders its own label for it. A
    stored name would be one language's words shown to every member of the
    project.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=60)
    profile: Literal["cloud", "device"]
    device_id: str | None = None
    whole_machine: bool = False

    @model_validator(mode="after")
    def resource_kind(self):
        # Only a named device keeps a name; see the class docstring.
        named_device = self.profile == "device" and self.device_id
        self.name = ((self.name or "").strip() or None) if named_device else None
        if self.profile == "cloud" and self.device_id:
            raise ValueError(say("computeCloudCannotNameDevice"))
        if self.profile == "device" and self.whole_machine:
            raise ValueError(say("computeWholeMachineIsCloud"))
        return self


class ProjectComputeConfigs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default: ComputeChoice


def standard_choice(profile: str | None = None) -> ComputeChoice:
    profile = profile or compute_default_name()
    if profile == "device":
        return ComputeChoice(profile="device")
    return ComputeChoice(profile="cloud")


def choice_label(choice: ComputeChoice) -> str:
    """The choice as a room line names it: the device's own name, or the
    platform's sentence for a choice that has none (rendered per reader)."""
    if choice.name:
        return choice.name
    if choice.profile == "device":
        return say("computeAnyDevice" if choice.device_id is None else "computeDevice")
    return say("computeCloudVm" if choice.whole_machine else "computeCloud")


def project_configs(project_settings: dict | None) -> ProjectComputeConfigs:
    raw = (project_settings or {}).get("compute_configs")
    if raw:
        return ProjectComputeConfigs.model_validate(raw)
    return ProjectComputeConfigs(default=standard_choice())


def room_choice(topic, project_settings: dict | None) -> ComputeChoice:
    """The work computer THIS ROOM works on — the room's own sessions and its
    tasks' until they fix their own (2026-09-28).

    一个话题一个容器（2026-09-28 的决定，推翻结论 60）：一间房只有一条算力选择，
    房间里坐着的每一条会话都工作在它算出来的那台机器上，所以会话要手的时候
    （``machine/session_work._attempt``）和闸门问「要谁的机器」的时候
    （``machine_policy_call``）读的都是它——不是会话行上那份副本，那份只是跟着它
    写、必须与它一致的记录。这条决定推翻了结论 60 里「手是 agent 的，不是房间
    的」那一半。

    The room's own choice once it has one — written the first time the room
    runs, so every later session gets the same full choice the first one did —
    and the project default until then.
    """
    if topic.compute_config:
        return ComputeChoice.model_validate(topic.compute_config)
    return project_configs(project_settings).default


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


async def choice_for_owner(
    session: AsyncSession, topic, task, project, owner_handle: str | None
) -> ComputeChoice:
    """The work computer a task works on under ``owner_handle``: its own choice,
    else its room's — skipping either when it names someone else's own
    computer — else the project default."""
    settings_ = project.settings if project else None
    default = project_configs(settings_).default
    for choice in (place_choice(topic, task, settings_), room_choice(topic, settings_)):
        if not choice.device_id or await works_tasks_of(
            session, choice.device_id, project, owner_handle
        ):
            return choice
    return default


async def fix_task_choice(session: AsyncSession, topic, task, project) -> bool:
    """Fix a task's work computer on its first turn that needs one, so a later
    change to the room's does not move it. The room's choice is copied, unless
    it names someone else's own computer: then the project default. False when
    there is nothing to fix: a room's own turn, or a task that already has its
    choice."""
    if task is None or task.compute_config:
        return False
    choice = await choice_for_owner(session, topic, task, project, task.owner_handle)
    # Written only if still unset: the owner may have picked one since this
    # turn read the task, and that pick stands.
    model = type(task)
    unset = or_(
        model.compute_config.is_(None),
        cast(model.compute_config, String) == "null",
    )
    written = await session.scalar(
        update(model)
        .where(model.id == task.id, unset)
        .values(compute_config=choice.model_dump())
        .returning(model.id)
        .execution_options(synchronize_session=False)
    )
    await session.refresh(task, ["compute_config"])
    return written is not None


def place_choice(topic, task, project_settings: dict | None) -> ComputeChoice:
    """The work computer a conversation works on: a task's own choice when its
    owner made one, else the room's (``room_choice``)."""
    if task is not None and task.compute_config:
        return ComputeChoice.model_validate(task.compute_config)
    return room_choice(topic, project_settings)


async def validate_choice(session: AsyncSession, project_id, choice: ComputeChoice):
    if choice.profile == "cloud":
        if not cloud_provisionable(settings):
            raise ValidationError(say("cloudNotAvailable"))
        if choice.whole_machine and not cloud_vm_provisionable(settings):
            raise ValidationError(say("cloudVmNotAvailable"))
        return
    devices = await sql_device_service(session).list_devices_for_project(project_id)
    if choice.device_id:
        if choice.device_id not in {d.device_id for d in devices}:
            raise ValidationError(say("deviceNotInProjectOrTeam"))
    elif not devices:
        raise ValidationError(say("teamHasNoDevices"))


async def bind_room_device_choice(
    session: AsyncSession, topic, project_settings: dict | None
):
    """Materialize a named project default before the device channel starts."""
    choice = room_choice(topic, project_settings)
    if choice.device_id:
        await validate_choice(session, topic.project_id, choice)
        devices = sql_device_service(session)
        if await devices.topic_binding(topic.id) is None:
            await devices.bind_topic_device(
                topic.id,
                choice.device_id,
                default_visibility(),
            )
    topic.compute_config = choice.model_dump()


async def machine_policy_call(
    session: AsyncSession, *, project, topic, choice: ComputeChoice
) -> gate.Call:
    """这个房间要的那台机器，写成闸门认得的那一次调用（结论 40 后半）。

    两处问它 —— 轮次组装（`agent/chat.py`，没人碰过选择器的房间走的就是它）和
    `PUT /topics/{id}/compute-profile`（人自己去点的那少数情形）。**必须是同一次调
    用**：提议的身份由「房间 + 资源 + subject + 档位 + 谁点头」算出来
    （`policy/proposals.identity`），两处给出不同的 subject 或 approver，同一个诉求
    就会变成两条提议、两个人各收一条，而结论 15 / 不变量 I11 说的是「一次」。所以
    它在这里构造一处，两处都调它。

    点头的是**那台机器的主人**：它花的是他的电和带宽，不是项目的钱。所以这里先把
    这一轮真正会落到哪台机器解析出来（`_machine_this_room_gets`，只读），再取它的
    owner；解析不出来（Cloud、或者一台在线的都没有）才退回项目的主人——Cloud 花的
    本来就是项目的钱，而一台都没有的房间下一步会在别处报出来。

    `label` 取那台机器的名字，取不到才用 `ComputeChoice.name`：进提议那句话的是给
    人看的名字，不是 `"device"` / `"cloud"` 这种池 id。
    """
    device = await _machine_this_room_gets(session, topic, choice)
    approver = project.owner_handle or ""
    if device is not None:
        owner = await session.get(UserRow, device.owner_user_id)
        if owner is not None:
            approver = owner.username
    return gate.Call(
        resource=gate.Resource.machine,
        subject=device.device_id if device is not None else choice.profile,
        label=device.name if device is not None else choice_label(choice),
        # `ComputeChoice.profile` 只有 `cloud` / `device` 两种，目录里两个都在。
        tier=COMPUTE_TIERS[choice.profile],
        approver=approver,
    )


async def _machine_this_room_gets(session: AsyncSession, topic, choice: ComputeChoice):
    """这一轮会落到哪台自托管机器上 —— 只读，一台也不占。

    顺序和真正去占的时候（`bind_room_device_choice` 之后的
    `device_provider.resolve_pinned_device`）一致：房间点了名的那台、已经绑上的那
    台、「系统挑一台」会挑中的那台。绝大多数房间没人打开过选择器，走的正是最后这一
    条——而它挑中的机器完全可能是别的成员的，所以闸门必须一路问到这里，否则「要那
    台机器的主人点头」在默认路径上根本不成立。

    Cloud 不是谁的机器，返回 `None`。
    """
    if choice.profile != COMPUTE_DEVICE:
        return None
    # 局部 import：`device_hub` 拉着连接器那一整套，模块级引它会把这个小模块的
    # import 面铺开一圈。
    from app.domain.agent.device_hub import device_hub

    devices = sql_device_service(session)
    if choice.device_id:
        return await devices.get_device(choice.device_id)
    binding = await devices.topic_binding(topic.id)
    if binding is not None:
        return await devices.get_device(binding.device_id)
    return await devices.first_healthy_device(topic.project_id, device_hub.is_online)
