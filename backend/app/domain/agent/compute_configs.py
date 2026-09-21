"""Project favorites and room-local resource choices."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ValidationError
from app.domain.agent.market import (
    COMPUTE_DEVICE,
    COMPUTE_TIERS,
    cloud_provisionable,
    compute_default_name,
)
from app.domain.device.wiring import sql_device_service
from app.domain.policy import gate
from app.domain.user.models import User as UserRow


class ComputeChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    profile: Literal["cloud", "device"]
    device_id: str | None = None
    cores: int | None = Field(default=None, ge=1, le=256)
    memory_mb: int | None = Field(default=None, ge=512, le=1048576)
    disk_gb: int | None = Field(default=None, ge=1, le=16384)

    @model_validator(mode="after")
    def resource_kind(self):
        if not self.name.strip():
            raise ValueError("配置名称不能为空")
        self.name = self.name.strip()
        if self.profile == "cloud" and self.device_id:
            raise ValueError("云配置不能指定自有设备")
        if self.profile == "device" and any(
            v is not None for v in (self.cores, self.memory_mb, self.disk_gb)
        ):
            raise ValueError("自有设备使用机器现有规格")
        return self


class ProjectComputeConfigs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default: ComputeChoice
    favorites: list[ComputeChoice] = Field(default_factory=list, max_length=12)


def standard_choice(profile: str | None = None) -> ComputeChoice:
    profile = profile or compute_default_name()
    if profile == "device":
        return ComputeChoice(name="自有设备 · 自动选择", profile="device")
    return ComputeChoice(name="云端 · 标准配置", profile="cloud")


def project_configs(project_settings: dict | None) -> ProjectComputeConfigs:
    raw = (project_settings or {}).get("compute_configs")
    if raw:
        return ProjectComputeConfigs.model_validate(raw)
    return ProjectComputeConfigs(default=standard_choice())


def room_choice(topic, project_settings: dict | None) -> ComputeChoice:
    if topic.compute_config:
        return ComputeChoice.model_validate(topic.compute_config)
    if topic.compute_profile:
        return standard_choice(topic.compute_profile)
    return project_configs(project_settings).default


async def validate_choice(session: AsyncSession, project_id, choice: ComputeChoice):
    if choice.profile == "cloud":
        if not cloud_provisionable(settings):
            raise ValidationError("云端尚未接入，暂不可用")
        return
    devices = await sql_device_service(session).list_devices_for_project(project_id)
    if choice.device_id:
        if choice.device_id not in {d.device_id for d in devices}:
            raise ValidationError("设备不属于当前项目或团队")
    elif not devices:
        raise ValidationError("团队暂无自有设备")


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
                await devices.binding_visibility(choice.device_id),
            )
    topic.compute_config = choice.model_dump()


async def machine_policy_call(
    session: AsyncSession, *, project, topic, choice: ComputeChoice
) -> gate.Call | None:
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

    目录不认识的池返回 `None`：那样的房间连 provider 都选不出来，下面那一步会把它
    说出口；闸门不替它报这个错，也不拿一个猜出来的档位去比。
    """
    tier = COMPUTE_TIERS.get(choice.profile)
    if tier is None:
        return None
    device = await _machine_this_room_gets(session, topic, choice)
    approver = project.owner_handle or ""
    if device is not None:
        owner = await session.get(UserRow, device.owner_user_id)
        if owner is not None:
            approver = owner.username
    return gate.Call(
        resource=gate.Resource.machine,
        subject=device.device_id if device is not None else choice.profile,
        label=device.name if device is not None else choice.name,
        tier=tier,
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
