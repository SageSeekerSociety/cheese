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
    """A saved role and optional model; None inherits the project main model."""

    body: str = ""
    skills: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)
    model: str | None = None


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
            # The pool's models are 档位 `included`: the gateway only offers what
            # it can bill (`gateway_catalog.offerable`), and what they cost the
            # project is already capped by the project key's `max_budget`. The
            # tiers a policy gates on are about spend a budget does NOT cap —
            # subscription quota, and a machine that belongs to somebody else.
            "tier": TIER_INCLUDED,
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
    # 问 ``harness_for`` 而不是 ``deployment_harness``：轮次组装（``chat.py`` 的
    # ``_assemble_turn``）和克隆（``topic/services.py`` 的 ``clone_from``）都按项
    # 目答，这里再按部署答一遍就是同一个问题的第二个答法。一套部署跑 claude-code、
    # 某个项目设置成 codex 时，轮次真跑在 codex 上，而目录会按 claude-code 的能力
    # 位筛——订阅别名是最直接的一类——于是 ``binding.resolve`` 挑得出一个 codex 指
    # 不到的模型绑上去，正好是这一筛要防的那件事。
    # 这个项目指向的骨架这套部署没注册（结论 43）时，它没有一个能用的模型：它的
    # 轮次在开始时就会在房间里说「没有部署」，目录跟着说同一件事——空的。
    running = HARNESSES.get(harness_for(project_settings))
    if running is None:
        return []
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
