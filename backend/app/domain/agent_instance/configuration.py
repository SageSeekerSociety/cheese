"""Saved agent configuration, and what a project may point an agent at.

Two questions live here, and which one is asked of which thing is the whole
design: **a harness is what can drive a model**, never the other way round.
``harness.Harness`` says why that direction, and what it cost when it was
written backwards.

So ``model_choices`` answers "what can this project run at all" with no opinion
about harnesses, and ``harness_choices`` answers "what can each harness here be
pointed at". Adding a harness touches the harness registry and nothing else.
"""

from dataclasses import asdict

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.errors import ValidationError
from app.domain.agent.harness import (
    DEFAULT_HARNESS,
    HARNESSES,
    Harness,
    harness_name,
    known_harness,
)
from app.domain.agent.market import subscription_model_ids, subscription_model_listings
from app.domain.agent.supply import GATEWAY, SUBSCRIPTION, resolve_pool


class AgentConfiguration(BaseModel):
    body: str = ""
    model: str = Field(min_length=1, max_length=128)
    harness: str = DEFAULT_HARNESS
    skills: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)
    effort: str | None = None


def model_choices(project_settings: dict | None) -> list[dict]:
    """Every model this project can be pointed at.

    ``supply`` is carried because it is a fact about the model that decides
    which harnesses can reach it — a subscription model comes with a credential
    only one harness can present — and reading it off the id later would mean
    guessing.
    """
    subscription_default = (
        resolve_pool(
            project_settings, subscription_enabled=settings.subscription_enabled
        )
        == SUBSCRIPTION
    ) and settings.subscription_enabled
    choices = (
        [
            dict(
                asdict(item),
                default=item.default and subscription_default,
                supply=SUBSCRIPTION,
            )
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
            "supply": GATEWAY,
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
    # Models an operator named for a harness that brings its own list, and that
    # the platform pool does not already serve. They reach the same gateway;
    # what makes them separate is that only that harness has an adapter for
    # them, which is exactly what `harness_choices` will say below.
    known = {item["id"] for item in choices}
    for named in dict.fromkeys(
        model for models in settings.agent_harness_models.values() for model in models
    ):
        if named in known or named in subscription_model_ids():
            continue
        choices.append(
            {
                "id": named,
                "label": named,
                "description": "平台模型池",
                "default": False,
                "supply": GATEWAY,
            }
        )
    return choices


def _drives(harness: Harness, model: dict) -> bool:
    """Can this harness be pointed at this model, in this deployment?"""
    if model["supply"] == SUBSCRIPTION:
        # The credential, not the shape: a subscription turn authenticates with
        # something minted for one harness, so no other can carry it even where
        # an operator has also listed the alias among its API models.
        return harness.carries_subscription
    if harness.speaks_gateway:
        return True
    return model["id"] in settings.agent_harness_models.get(harness.name, [])


def harness_choices(project_settings: dict | None) -> list[dict]:
    """每个 harness，以及它在这个项目里能被指向哪些模型。

    A harness with nothing to drive is not offered at all: a deployment that
    serves no model a harness supports does not have that harness, whatever the
    registry says, and letting a person pick it would produce a teammate that
    cannot take a turn.
    """
    models = model_choices(project_settings)
    choices = []
    for harness in HARNESSES.values():
        driveable = [item["id"] for item in models if _drives(harness, item)]
        if not driveable:
            continue
        choices.append(
            {
                "id": harness.name,
                "label": harness.label,
                "description": "",
                "default": harness.name == DEFAULT_HARNESS,
                "models": driveable,
            }
        )
    return choices


def models_for(harness: str | None, project_settings: dict | None) -> list[str]:
    """The models this harness can be pointed at here, in preference order."""
    name = harness_name(harness)
    for choice in harness_choices(project_settings):
        if choice["id"] == name:
            return choice["models"]
    return []


def initial_model(project_settings: dict | None, harness: str | None = None) -> str:
    """What a new agent on this harness starts pointed at.

    The project's own default when that harness can drive it — a preset asking
    for a different harness must not silently move the project off the model it
    picked — and otherwise the first thing that harness CAN drive.
    """
    driveable = models_for(harness, project_settings)
    if not driveable:
        raise ValidationError(
            f"当前项目没有 {_label(harness)} 能用的模型，请检查模型服务"
        )
    preferred = {
        item["id"] for item in model_choices(project_settings) if item["default"]
    }
    return next((model for model in driveable if model in preferred), driveable[0])


def _label(harness: str | None) -> str:
    entry = HARNESSES.get(harness_name(harness))
    return entry.label if entry else harness_name(harness)


def validate_configuration(
    config: AgentConfiguration, project_settings: dict | None
) -> None:
    """Refuse a configuration this deployment cannot actually run.

    Asked in this order on purpose. Three different things can be wrong — the
    harness does not exist here, the project cannot use that model at all, the
    harness cannot drive it — and each has its own answer. Reporting the second
    for the third is how this read before the direction was fixed: 「当前项目无法
    使用模型 X」 about a model the project could use perfectly well, sending the
    person to change the half they had chosen on purpose.
    """
    if not known_harness(config.harness):
        raise ValidationError(f"当前平台无法使用运行方式 {config.harness!r}")
    if config.model not in {item["id"] for item in model_choices(project_settings)}:
        raise ValidationError(f"当前项目无法使用模型 {config.model!r}，请选择可用模型")
    driveable = models_for(config.harness, project_settings)
    if not driveable:
        raise ValidationError(
            f"当前项目没有 {_label(config.harness)} 能用的模型，请检查模型服务"
        )
    if config.model not in driveable:
        raise ValidationError(
            f"{_label(config.harness)} 不能运行模型 {config.model!r}，"
            "请换一个模型或运行方式"
        )
