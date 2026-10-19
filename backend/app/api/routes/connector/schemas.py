"""connector 的线上形状：设备流与「我的设备 / Agent」的请求与响应模型。

原来散在单文件 ``app/api/routes/connector.py`` 各段之间的 ``BaseModel`` 集中在这里，
字段与注释逐行原样搬来 —— 形状只有一处可看。谁用哪个模型见各子模块的 import。
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.domain.device.login_kind import ConnectionKind


class DeviceStartRequest(BaseModel):
    device_name: str | None = None


class DevicePollRequest(BaseModel):
    device_code: str


class RunningDeviceSessionOut(BaseModel):
    session_id: str
    agent_name: str
    agent_name_source: str | None
    project_id: str
    project_name: str
    team_id: int
    team_name: str
    conversation_id: str
    conversation_kind: Literal["room", "task", "thread"]
    conversation_title: str
    room_id: str
    roles: list[Literal["center", "executor"]]
    since: str


class RunningDeviceSessionsOut(BaseModel):
    sessions: list[RunningDeviceSessionOut]


class ConnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_code: str
    # The human-chosen name for this compute node (optional — blank keeps the name the
    # cli proposed at device-flow start, avoiding an "unnamed" node).
    device_name: str | None = None
    team_id: int | None = None


class RenameDeviceRequest(BaseModel):
    name: str


class ClaudeCodeLoginOut(BaseModel):
    """What a machine last said about its owner's Claude Code login."""

    id: str
    local_id: str
    installed: bool
    logged_in: bool
    #: `claude.ai` for a subscription, the API key kind, or `model_service`.
    auth_method: str | None
    subscription_type: str | None
    model: str | None
    service_host: str | None
    #: What its owner named the connection; None until they do.
    name: str | None
    checked_at: str
    #: Removed while the machine was away; it logs out as it next connects.
    revoking: bool


class ModelConnectionOut(ClaudeCodeLoginOut):
    device_id: str
    device_name: str
    online: bool
    #: When a session of the owner's Claude Code last ran on that machine.
    last_used_at: str | None


class DeviceScreenOut(BaseModel):
    sid: str
    agent_handle: str
    agent_user_id: str
    project_id: str | None
    topic_id: str | None
    agent_name: str | None
    agent_name_source: str | None


class EnrollmentDeviceOut(BaseModel):
    device_id: str
    name: str
    online: bool
    team_id: int
    last_seen_at: str | None
    screens: list[DeviceScreenOut]
    logins: list[ClaudeCodeLoginOut]


class AccessibleDevicesOut(BaseModel):
    devices: list[EnrollmentDeviceOut]


class AccountConnectionOut(BaseModel):
    """A model connection the caller's account holds: an Anthropic API key or
    a model service, kept sealed by the platform. Never the key."""

    id: str
    kind: ConnectionKind
    #: What its owner calls it.
    name: str | None
    #: The key's last four characters, to tell keys apart.
    key_last4: str
    #: A model service's host and model; None for an API key.
    service_host: str | None
    model: str | None
    #: When the service last answered a check, and its refusal if it refused.
    checked_at: str | None
    check_error: str | None
    created_at: str


class ModelConnectionsOut(BaseModel):
    #: The logins the caller's machines hold.
    connections: list[ModelConnectionOut]
    #: The connections the caller's account holds.
    account_connections: list[AccountConnectionOut]


class AccountConnectionIn(BaseModel):
    """An API key or a model service to keep on the caller's account."""

    kind: Literal[ConnectionKind.api_key, ConnectionKind.model_service]
    key: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)
    ]
    #: A model service's address and model; an API key calls Anthropic.
    base_url: (
        Annotated[
            str,
            StringConstraints(
                strip_whitespace=True, pattern=r"^https://[^\s/]+\S*$", max_length=512
            ),
        ]
        | None
    ) = None
    model: (
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, pattern=r"^\S+$", max_length=128),
        ]
        | None
    ) = None
    name: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)] | None
    ) = None


class ConnectionRename(BaseModel):
    """What its owner calls a model connection; empty takes the name away."""

    name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)]


class ModelServiceRequest(BaseModel):
    base_url: Annotated[
        str, StringConstraints(strip_whitespace=True, pattern=r"^https?://[^\s/]+")
    ]
    token: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    model: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\S+$")]
