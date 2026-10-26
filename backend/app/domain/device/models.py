"""SQLAlchemy models backing the device flow (SQL repository).

Tables mirror the storage-agnostic dataclasses in ``repository.py``:
``device`` (shared connector identity), ``hosted_device`` (human-owned machines),
``device_auth_code`` (short-lived device-flow codes) and
``device_health``
(a machine's rolling failure streak / quarantine — #186) and
``claude_logins`` (each login its owner's own Claude Code has for the platform
there — #2991) and ``device_sandbox`` (whether it can give a session a
sandbox). ``SqlDeviceRepository``
converts between these rows and the dataclasses; the service never sees them.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.device.login_kind import FIRST_LOGIN
from app.domain.device.supply import Supply


class DeviceRow(Base):
    __tablename__ = "device"

    device_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    cloud_control_private: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    # The SHA-256 digest of the durable device token (`user.tokens.digest_token`).
    # The token itself is handed to the machine once, when it is minted
    # (`DeviceService.issue_token`), and stored nowhere: a read of this table
    # cannot act as a device. None until then.
    token_digest: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    # Enrollment provenance; device management is authorized by team_id.
    team_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("team.id", name="fk_device_team_id_team"),
        nullable=False,
        index=True,
    )
    owner_user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("user.id", ondelete="SET NULL", name="fk_device_owner_user_id_user"),
        nullable=True,
        index=True,
    )
    # NOTE: a device is PURE COMPUTE (execution-architecture v3: AIPool ⊥ ComputePool;
    # a self-hosted device is a ComputePool node). It carries NO agent identity — the
    # agent a screen acts as is resolved per project/topic (fusion-design §5: agent =
    # screen), independent of which machine hosts it.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # When the server last heard from this machine: written as it connects and
    # at most once a minute while connected; null until it first connects.
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Supply is the stored lifecycle fact: who brought the machine in.
    supply: Mapped[Supply] = mapped_column(
        Enum(Supply, native_enum=False, length=16),
        default=Supply.self_hosted,
        server_default=Supply.self_hosted.value,
        nullable=False,
    )


class HostedDeviceRow(Base):
    """The human-owned, persistent subtype of a connector ``device``."""

    __tablename__ = "hosted_device"

    device_id: Mapped[str] = mapped_column(
        ForeignKey("device.device_id", ondelete="CASCADE"), primary_key=True
    )
    owner_user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "user.id", ondelete="SET NULL", name="fk_hosted_device_owner_user_id_user"
        ),
        nullable=True,
        index=True,
    )


class DeviceAuthCodeRow(Base):
    __tablename__ = "device_auth_code"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    device_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # pending | approved
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class DeviceHealthRow(Base):
    """Rolling health of one compute machine — the basis for "this body is dead".

    One row per device, written only when something goes wrong: a turn that fails
    with a HOST-SCOPED platform failure (disk full, machine unreachable) bumps
    ``consecutive_failures``; any turn that gets through deletes the row. When the
    streak reaches the threshold the device is QUARANTINED (``quarantined_until``)
    rather than destroyed — a full disk heals on its own once other topics finish,
    and destroying a machine to fix a ten-minute problem is the expensive way to be
    wrong. ``last_failure_code`` makes the streak per-cause: two different failures
    in a row are two accidents, not a dying machine.
    """

    __tablename__ = "device_health"

    device_id: Mapped[str] = mapped_column(
        ForeignKey("device.device_id", ondelete="CASCADE"), primary_key=True
    )
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    last_failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    quarantined_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


def _login_record_id(context) -> str:
    """A login's record id: the machine's own for its first login, which named
    that login before a machine could hold several (own agents and links made
    then still say it), and one of its own for every other."""
    given = context.get_current_parameters()
    # ``local_id``'s own default is not applied yet when this one runs.
    if (given.get("local_id") or FIRST_LOGIN) == FIRST_LOGIN:
        return given.get("device_id") or uuid.uuid4().hex
    return uuid.uuid4().hex


class DeviceClaudeLoginRow(Base):
    """One login the owner's own Claude Code has for the platform on a machine
    (`cheesehost claude login`), as the machine last answered. A machine holds
    any number of them, each in a directory of its own named by ``local_id``.

    Written each time the backend asks the machine — when it connects, and when
    its owner asks again. The machine is the source of truth; this is what it
    said, and when. An own agent runs on one of these (`OwnAgent.login_id`).
    """

    __tablename__ = "claude_logins"
    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("team.id", ondelete="CASCADE", name="fk_claude_logins_team_id_team"),
        nullable=True,
        index=True,
    )
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "user.id", ondelete="CASCADE", name="fk_claude_logins_owner_user_id_user"
        ),
        nullable=True,
        index=True,
    )
    host_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "model_credential_hosts.id",
            ondelete="CASCADE",
            name="fk_claude_logins_host_id_model_credential_hosts",
        ),
        nullable=True,
        index=True,
    )
    allowed_models: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    __table_args__ = (
        UniqueConstraint(
            "device_id", "local_id", name="uq_claude_logins_device_id_local_id"
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=_login_record_id
    )
    device_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "device.device_id",
            ondelete="SET NULL",
            name="fk_claude_logins_device_id_device",
        ),
        nullable=True,
    )
    # What the machine calls it: the name of its directory there
    # (`login_kind.FIRST_LOGIN` for the first one).
    local_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default=FIRST_LOGIN
    )
    # The pinned build is on the machine. Without it nothing is logged in yet.
    installed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    logged_in: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Subscription status reported by Claude Code, and its plan (`max`, `pro`, …).
    auth_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    subscription_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Which Claude account it is — the email and organization `claude auth
    # status` names — so several logins on one machine can be told apart.
    # None until the machine is next asked.
    account_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    account_org: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # What its owner named this connection on 「模型连接」; None until they do.
    # The machine never reports it, so its reports leave it alone, and the own
    # agents on the connection are named after it.
    name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # When its owner removed this login from a browser while the machine was
    # away. Until the machine has logged it out — the next time it connects —
    # the login counts as logged out (``logged_in`` stays false whatever the
    # machine says), so nothing starts on it; None once that is done.
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def transport_id(self) -> str:
        return self.host_id or self.device_id or ""


class ModelCredentialHost(Base):
    """A model-process connection, with no execution-device enrollment."""

    __tablename__ = "model_credential_hosts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # The digest of the token the host connects with, as on ``DeviceRow``.
    token_digest: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255))


class DeviceSandboxRow(Base):
    """Whether a machine a person enrolled can give a session a sandbox, as it
    last answered: asked when it connects and when its owner asks again
    (`device.sandbox`). The machine runs the very check its install runs, so
    the answer is the install's own; ``reason`` is the sentence it said when it
    cannot, for its owner, as ``{"key", "params"}`` JSON (`device.sandbox`).
    """

    __tablename__ = "device_sandbox"

    device_id: Mapped[str] = mapped_column(
        ForeignKey(
            "device.device_id", ondelete="CASCADE", name="fk_device_sandbox_device_id"
        ),
        primary_key=True,
    )
    isolates: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
