"""Project favorites and room-local resource choices."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ValidationError
from app.domain.agent.market import cloud_provisionable, compute_default_name
from app.domain.device.wiring import sql_device_service


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
    from app.domain.device.supply import Visibility

    choice = room_choice(topic, project_settings)
    if choice.device_id:
        await validate_choice(session, topic.project_id, choice)
        devices = sql_device_service(session)
        if await devices.topic_binding(topic.id) is None:
            await devices.bind_topic_device(topic.id, choice.device_id, Visibility.host)
    topic.compute_config = choice.model_dump()
