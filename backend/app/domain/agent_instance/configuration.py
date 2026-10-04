"""Saved teammate configuration and the models available to a project."""

from dataclasses import asdict

from pydantic import BaseModel, Field

from app.core.config import settings
from app.domain.agent import gateway_catalog
from app.domain.agent.harness import HARNESSES, Harness, harness_for
from app.domain.agent.market import (
    TIER_INCLUDED,
    subscription_model_ids,
    subscription_model_listings,
)
from app.domain.agent.supply import GATEWAY, SUBSCRIPTION, resolve_pool


class AgentConfiguration(BaseModel):
    """A saved role and optional model; None inherits the project main model.

    MCP servers are not here: they belong to the agent's type, and a session
    reads them from there (`agent_instance.services.type_of_seat`).
    """

    body: str = ""
    skills: list[str] = Field(default_factory=list)
    model: str | None = None


def project_pool(project_settings: dict | None) -> str:
    """这个项目跑哪个池。

    ``resolve_pool`` 对目录消费者的唯一出口：「这轮走哪条供给」只能有一个
    答案来源（P34 的守卫按模块数引用），要池的调用方拿这里，不许自己再
    import supply。
    """
    return resolve_pool(project_settings)


def model_choices(project_settings: dict | None) -> list[dict]:
    """Every model this project can be pointed at.

    ``supply`` is carried because it is a fact about the model that decides
    which harnesses can reach it — a subscription model comes with a credential
    only one harness can present — and reading it off the id later would mean
    guessing.
    """
    subscription_default = resolve_pool(project_settings) == SUBSCRIPTION
    choices = [
        dict(
            asdict(item),
            default=item.default and subscription_default,
            supply=SUBSCRIPTION,
        )
        for item in subscription_model_listings()
    ]
    # The pool's models come from the gateway, which is the only thing that
    # knows: it needs a route and a price to serve one at all, so a list kept
    # here could only ever be a second copy drifting out of step with the first.
    choices.extend(
        {
            "id": item.id,
            "label": item.label,
            "description": "平台模型池",
            "default": not subscription_default and item.id == settings.agent_model,
            "supply": GATEWAY,
            # The tier an administrator set on the gateway (`cheese_tier`):
            # which plans may use it.
            "tier": item.tier,
        }
        for item in gateway_catalog.offerable()
    )
    choices = list({item["id"]: item for item in choices}.values())
    # Models an operator named for a harness that brings its own list, and that
    # the platform pool does not already serve. They reach the same gateway;
    # what makes them separate is that only that harness has an adapter for
    # them — which is what the filter below asks of the one this deployment
    # runs.
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
                "tier": TIER_INCLUDED,
            }
        )
    # 按这个项目真会跑的骨架筛。跑哪个骨架是部署设置加项目设置答的（结论 28），
    # 所以这一筛问的是那一份设置，不是任何一个参与者——一个骨架指不到的模型，在
    # 这个项目里根本不是一个能用的模型，列出来只会让绑上它的那条活在派出去的那
    # 一刻才失败。
    #
    # 问 ``harness_for``：轮次组装（``room/turn.py`` 的 ``_assemble_turn``）和克隆
    # （``topic/services.py`` 的 ``clone_from``）都按项目答，这里只按部署答就是同
    # 一个问题的第二个答法。一套部署列了 claude-code 和 pi、某个项目指定了 pi 时，轮次真
    # 跑在 pi 上，而目录会按 claude-code 的能力位筛——订阅别名是最直接的一类——于
    # 是 ``binding.resolve`` 挑得出一个 pi 指不到的模型绑上去，正好是这一筛要防的
    # 那件事。
    running = HARNESSES[harness_for(project_settings)]
    choices = [item for item in choices if _drives(running, item)]
    # Mark the available selection for presentation. Runtime callers pass the
    # saved value explicitly to binding.resolve so an unavailable selection is
    # refused rather than silently becoming this catalog's deployment default.
    chosen = (project_settings or {}).get("default_model")
    known_ids = {item["id"] for item in choices}
    if isinstance(chosen, str) and chosen in known_ids:
        for item in choices:
            item["default"] = item["id"] == chosen
    return choices


def _drives(harness: Harness, model: dict) -> bool:
    """Can this harness be pointed at this model, in this deployment?

    ``harness`` is the one this project runs — the deployment's unless the
    project's own settings say otherwise.
    """
    if model["supply"] == SUBSCRIPTION:
        # The credential, not the shape: a subscription turn authenticates with
        # something minted for one harness, so no other can carry it even where
        # an operator has also listed the alias among its API models.
        return harness.carries_subscription
    if harness.speaks_gateway:
        return True
    return model["id"] in settings.agent_harness_models.get(harness.name, [])
