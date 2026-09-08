"""Saved agent configuration and project-scoped model choices."""

from dataclasses import asdict

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.errors import ValidationError
from app.domain.agent.harness import DEFAULT_HARNESS, known_harness
from app.domain.agent.market import subscription_model_listings
from app.domain.agent.supply import SUBSCRIPTION, resolve_pool


class AgentConfiguration(BaseModel):
    body: str = ""
    model: str = Field(min_length=1, max_length=128)
    harness: str = DEFAULT_HARNESS
    skills: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)
    effort: str | None = None


def model_choices(project_settings: dict | None) -> list[dict]:
    if (
        resolve_pool(
            project_settings, subscription_enabled=settings.subscription_enabled
        )
        == SUBSCRIPTION
    ):
        if not settings.subscription_enabled:
            return []
        return [asdict(item) for item in subscription_model_listings()]
    return [
        {
            "id": settings.agent_model,
            "label": settings.agent_model,
            "description": "平台模型池",
            "default": True,
        }
    ]


def initial_model(project_settings: dict | None) -> str:
    choices = model_choices(project_settings)
    if not choices:
        raise ValidationError("当前项目没有可用模型，请检查模型服务")
    return next(item["id"] for item in choices if item["default"])


def validate_configuration(
    config: AgentConfiguration, project_settings: dict | None
) -> None:
    if config.model not in {item["id"] for item in model_choices(project_settings)}:
        raise ValidationError(f"当前项目无法使用模型 {config.model!r}，请选择可用模型")
    if not known_harness(config.harness):
        raise ValidationError(f"当前平台无法使用运行方式 {config.harness!r}")
