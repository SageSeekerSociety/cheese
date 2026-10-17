"""Project scripts and the immutable configuration a room starts with."""

import hashlib
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.sentences import say
from app.domain.project.models import Project
from app.domain.topic.models import Topic, TopicKind, TopicStatus


@dataclass(frozen=True, slots=True)
class EnvironmentProject:
    id: uuid.UUID
    settings: dict | None


@dataclass(frozen=True, slots=True)
class EnvironmentRoom:
    id: uuid.UUID
    title: str
    resource_id: uuid.UUID | None
    archived_at: datetime | None
    is_overview: bool
    environment: dict | None


# Membership supplies this read so project data access does not compose services.
VisibleRooms = Callable[[list[Topic]], Awaitable[list[Topic]]]


def _room(row: Topic) -> EnvironmentRoom:
    return EnvironmentRoom(
        id=row.id,
        title=row.title,
        resource_id=row.resource_id,
        archived_at=row.archived_at,
        is_overview=row.kind == TopicKind.root,
        environment=row.environment,
    )


async def load_environment_project(
    session: AsyncSession, project_id: uuid.UUID
) -> EnvironmentProject:
    project = await session.get(Project, project_id)
    if project is None:
        raise NotFoundError("Project not found")
    return EnvironmentProject(project.id, project.settings)


async def save_project_environment(
    session: AsyncSession, project_id: uuid.UUID, config: dict
) -> None:
    project = await session.scalar(
        select(Project).where(Project.id == project_id).with_for_update(key_share=True)
    )
    if project is None:
        raise NotFoundError("Project not found")
    project.settings = {**(project.settings or {}), "environment": config}
    await session.flush()


async def load_environment_room(
    session: AsyncSession,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    *,
    lock: bool = False,
    visible: VisibleRooms | None = None,
) -> EnvironmentRoom:
    statement = select(Topic).where(
        Topic.id == topic_id,
        Topic.project_id == project_id,
        Topic.is_private.is_(False),
    )
    if lock:
        # The session keeps objects across commit; a locked re-read must refresh.
        statement = statement.with_for_update().execution_options(
            populate_existing=True
        )
    topic = await session.scalar(statement)
    if topic is None or (visible is not None and not await visible([topic])):
        raise NotFoundError("Room not found")
    return _room(topic)


async def rooms_with_environment(
    session: AsyncSession, project_id: uuid.UUID, *, visible: VisibleRooms
) -> list[EnvironmentRoom]:
    topics = await session.scalars(
        select(Topic)
        .where(
            Topic.project_id == project_id,
            Topic.is_private.is_(False),
            Topic.kind != TopicKind.root,
            Topic.status != TopicStatus.archived,
        )
        .order_by(Topic.created_at)
    )
    return [_room(topic) for topic in await visible(list(topics))]


async def set_room_environment(
    session: AsyncSession, topic_id: uuid.UUID, config: dict
) -> None:
    """Write the room's pin in the caller's transaction, after its idle check."""
    topic = await session.get(Topic, topic_id)
    if topic is None:
        raise NotFoundError("Room not found")
    topic.environment = config
    await session.flush()


class EnvironmentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    setup_script: str = Field(default="", max_length=65536)
    startup_script: str = Field(default="", max_length=65536)
    variables: dict[str, str] = Field(default_factory=dict, max_length=100)

    @field_validator("setup_script", "startup_script")
    @classmethod
    def script_text(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError(say("environmentScriptNul"))
        return value.replace("\r\n", "\n")

    @field_validator("variables")
    @classmethod
    def project_variables(cls, values: dict[str, str]) -> dict[str, str]:
        reserved = {
            "HOME",
            "PATH",
            "PWD",
            "OLDPWD",
            "SHELL",
            "USER",
            "LOGNAME",
            "BASH_ENV",
            "ENV",
            "SHELLOPTS",
            "BASHOPTS",
            "CDPATH",
            "IFS",
            "PYTHONPATH",
            "PYTHONHOME",
            "NODE_OPTIONS",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "REQUESTS_CA_BUNDLE",
            "CURL_CA_BUNDLE",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "NODE_EXTRA_CA_CERTS",
            "TMUX",
            "TMUX_PANE",
        }
        for key, value in values.items():
            upper = key.upper()
            if (
                not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
                or upper in reserved
                or upper.startswith(
                    ("CHEESE_", "CLAUDE_", "ANTHROPIC_", "LD_", "DYLD_")
                )
            ):
                raise ValueError(say("environmentVariableNameInvalid", name=key))
            if "\x00" in value or len(value) > 16384:
                raise ValueError(say("environmentVariableValueInvalid", name=key))
        return values

    def snapshot(self) -> dict:
        data = self.model_dump()
        revision = hashlib.sha256(
            json.dumps(data, sort_keys=True, ensure_ascii=True).encode()
        ).hexdigest()
        return {**data, "revision": revision}


def project_environment(settings: dict | None) -> dict:
    stored = (settings or {}).get("environment") or {}
    return EnvironmentConfig.model_validate(
        {key: value for key, value in stored.items() if key != "revision"}
    ).snapshot()


async def pin_environment(
    session: AsyncSession, project_id: uuid.UUID, topic_id: uuid.UUID
) -> dict:

    topic = await session.scalar(
        select(Topic)
        .where(Topic.id == topic_id, Topic.project_id == project_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if topic is None:
        raise NotFoundError("Room not found")
    if topic.environment is None:
        project = await session.get(Project, project_id)
        if project is None:
            raise NotFoundError("Project not found")
        topic.environment = project_environment(project.settings)
        await session.flush()
    return dict(topic.environment)
