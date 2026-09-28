"""Project machine request/response schemas (Pydantic v2)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.domain.machine.models import MAX_ENROLL_ATTEMPTS, AiStatus, MachineStatus


class MachineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    # None while the provider is still creating the machine.
    machine_id: int | None
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
    # Enrollment: the cheese device this machine became, once it has. Until
    # then, why the last attempt didn't take.
    device_id: str | None
    enrolled_at: datetime | None
    enroll_error: str | None
    enroll_attempts: int
    enroll_max_attempts: int = MAX_ENROLL_ATTEMPTS
    requested_by: str | None
    # When MicroCloud last answered about this machine. Reported so a stale
    # reading is visible as stale rather than presented as current.
    last_seen_at: datetime | None
    created_at: datetime
