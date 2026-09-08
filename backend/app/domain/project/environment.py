"""Project scripts and the immutable configuration a room starts with."""

import hashlib
import json
import re
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError
from sqlalchemy.ext.asyncio import AsyncSession


class EnvironmentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    setup_script: str = Field(default="", max_length=65536)
    startup_script: str = Field(default="", max_length=65536)
    variables: dict[str, str] = Field(default_factory=dict, max_length=100)

    @field_validator("setup_script", "startup_script")
    @classmethod
    def script_text(cls, value: str) -> str:
        if "\x00" in value:
            raise PydanticCustomError(
                "environment_script", "scripts cannot contain NUL"
            )
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
                raise PydanticCustomError(
                    "environment_variable",
                    "environment variable {name} is reserved or invalid",
                    {"name": key},
                )
            if "\x00" in value or len(value) > 16384:
                raise PydanticCustomError(
                    "environment_variable",
                    "environment variable {name} has an invalid value",
                    {"name": key},
                )
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
    from sqlalchemy import select

    from app.core.errors import NotFoundError
    from app.domain.project.models import Project
    from app.domain.topic.models import Topic

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
