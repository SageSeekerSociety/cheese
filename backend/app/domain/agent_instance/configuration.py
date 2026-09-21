"""一个 agent 存着的角色，和这个项目能用哪些模型。

**模型不在这里。** 它是一件工作占用的资源，绑在活上（结论 3、44，
``room_task/binding.py``），不是参与者的属性——「想换模型就再建一个 agent」是
把资源当成了参与者，同一个人换了把锤子不会变成另一个人。**骨架也不在这里**：
它是部署的开发者选项，普通用户看不见（结论 28）。

所以这里只剩一个问题：``model_choices`` 答「这套部署里，这个项目能被指向哪些
模型」。**按骨架筛是部署这一级的事**（结论 3：「部署决定可选列表（按 harness
筛）；项目定默认和档位策略」）——能不能拿订阅凭据、说不说网关那套话，是骨架的
事实，不是模型的；``harness.Harness`` 写了这个方向为什么只能是这一个。
"""

from dataclasses import asdict

from pydantic import BaseModel, Field

from app.core.config import settings
from app.domain.agent import gateway_catalog
from app.domain.agent.harness import DEFAULT_HARNESS, HARNESSES, Harness
from app.domain.agent.market import subscription_model_ids, subscription_model_listings
from app.domain.agent.supply import GATEWAY, SUBSCRIPTION, resolve_pool


class AgentConfiguration(BaseModel):
    """一个 agent 存着的角色：人设、技能、外部工具。

    ``model``/``harness``/``effort`` 还留在这张 schema 上，但**读不出、也写不
    出**：``exclude=True`` 让它们不再进 ``model_dump()``，所以没有一处代码再把
    这三个键写回库里，也没有一处再从这张 schema 上读它们。

    为什么不干脆删掉：字段本身和那条把三个键从库里清干净的迁移在 P15b 一起
    走，而那条迁移跑完到换完容器之间，在跑的是**这一版**镜像——它读到一行没有
    这三个键的 ``configuration`` 必须照常构造得出来。可选就是这件事。
    """

    body: str = ""
    skills: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)
    model: str | None = Field(default=None, exclude=True)
    harness: str | None = Field(default=None, exclude=True)
    effort: str | None = Field(default=None, exclude=True)


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
            }
        )
    # 按这套部署跑的骨架筛。跑哪个骨架是部署的选择（结论 28），所以这一筛问的
    # 是部署，不是任何一个参与者——一个骨架指不到的模型，在这套部署里根本不是一
    # 个能用的模型，列出来只会让绑上它的那条活在派出去的那一刻才失败。
    running = HARNESSES[DEFAULT_HARNESS]
    return [item for item in choices if _drives(running, item)]


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
