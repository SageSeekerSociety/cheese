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
    subscription_default = (
        resolve_pool(
            project_settings, subscription_enabled=settings.subscription_enabled
        )
        == SUBSCRIPTION
    ) and settings.subscription_enabled
    choices = (
        [
            dict(asdict(item), default=item.default and subscription_default)
            for item in subscription_model_listings()
        ]
        if settings.subscription_enabled
        else []
    )
    choices.extend(
        {
            "id": model,
            "label": label,
            "description": "平台模型池",
            "default": not subscription_default and model == settings.agent_model,
        }
        for model, label in dict.fromkeys(
            [
                (settings.agent_model, settings.agent_model),
                ("deepseek-flash", "DeepSeek V4.1 Flash"),
                ("glm-5.2", "GLM-5.2"),
            ]
        )
    )
    choices = list({item["id"]: item for item in choices}.values())
    for item in choices:
        item["harnesses"] = [DEFAULT_HARNESS]
    for model in dict.fromkeys(settings.agent_codex_models):
        existing = next((item for item in choices if item["id"] == model), None)
        if existing is not None:
            existing["harnesses"].append("codex")
        else:
            choices.append(
                dict(
                    id=model,
                    label=model,
                    description="平台模型池",
                    default=False,
                    harnesses=["codex"],
                )
            )
    return choices


def initial_model(project_settings: dict | None) -> str:
    choices = model_choices(project_settings)
    if not choices:
        raise ValidationError("当前项目没有可用模型，请检查模型服务")
    return next(item["id"] for item in choices if item["default"])


def validate_configuration(
    config: AgentConfiguration, project_settings: dict | None
) -> None:
    if config.model not in {
        item["id"]
        for item in model_choices(project_settings)
        if config.harness in item["harnesses"]
    }:
        raise ValidationError(f"当前项目无法使用模型 {config.model!r}，请选择可用模型")
    if not known_harness(config.harness):
        raise ValidationError(f"当前平台无法使用运行方式 {config.harness!r}")
