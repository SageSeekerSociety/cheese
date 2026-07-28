"""Project machine request/response schemas (Pydantic v2)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.machine.models import AiStatus, MachineStatus


class MachineCreate(BaseModel):
    # The key authorised on the machine for `login_user`. Optional because
    # MicroCloud allows a machine with no key — it is simply unreachable by a
    # human until one is added, which is a legitimate "just give me compute".
    ssh_pubkey: str | None = Field(default=None, alias="sshPubkey")
    login_user: str | None = Field(default=None, alias="loginUser")
    cores: int | None = Field(default=None, ge=1)
    memory_mb: int | None = Field(default=None, alias="memoryMb", ge=128)
    disk_gb: int | None = Field(default=None, alias="diskGb", ge=2)

    model_config = ConfigDict(populate_by_name=True)


class MachineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    machine_id: int
    hostname: str
    login_user: str
    cores: int
    memory_mb: int
    disk_gb: int
    status: MachineStatus
    ip: str | None
    # Reported separately because they settle separately: a machine can be
    # `running` with its agent access still `provisioning`.
    ai_mode: str
    ai_status: AiStatus
    requested_by: str | None
    created_at: datetime
