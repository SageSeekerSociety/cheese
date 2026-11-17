"""Every model a project can be pointed at, and how each one is reached.

It reads five facts that all live in this package: what the gateway currently
offers (`gateway_catalog`), what a subscription brings (`market`), the names of
the pools that serve them (`supply`), which harness its turns run (`harness`),
and the deployment's own settings. A picker shows the answer and admission goes through
it to decide whether a selection is real, so it is computed once per project
(`catalog`) and handed down as a value.

It sits here rather than beside `AgentConfiguration`, which also talks about a
model: what models EXIST is a property of the deployment and the project, not of
a saved teammate, and reading the gateway or the harnesses
from a package that is not this one is a dependency back into `agent` — the
cycle C3 counts. The one reader that needs the catalogue (`agent_instance`
validating a chosen model id when a teammate is created or edited) takes it as
a callable handed in by the caller, the way `project.ports.ComputeCredits` is.
"""

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import get_args

from app.core import model_roles
from app.core.config import settings
from app.core.errors import ValidationError
from app.domain.agent import gateway_catalog
from app.domain.agent.harness import HARNESSES, Harness, harness_for
from app.domain.agent.market import (
    TIER_INCLUDED,
    subscription_model_alias,
    subscription_model_ids,
    subscription_model_listings,
)
from app.domain.agent.supply import GATEWAY, SUBSCRIPTION
from app.domain.agent_instance.configuration import Effort
from app.domain.policy import gate
from app.domain.room_task import binding
from app.domain.room_task.binding import WorkBinding

#: What each subscription model honours: Claude Code reads Claude's own
#: capabilities, so every effort the platform offers reaches the model.
SUBSCRIPTION_EFFORTS = list(get_args(Effort))


def model_choices(project_settings: dict | None) -> list[dict]:
    """Every model this project can be pointed at.

    ``supply`` is carried because it is a fact about the model that decides
    which harnesses can reach it — a subscription model comes with a credential
    only one harness can present — and reading it off the id later would mean
    guessing.

    The default is the gateway model the platform names for agents
    (`model_roles`); a subscription model is never the default. Which pool a
    turn takes follows from the model it is bound to, never from the project.
    """
    choices = [
        dict(
            asdict(item),
            default=False,
            supply=SUBSCRIPTION,
            efforts=SUBSCRIPTION_EFFORTS,
            # Claude: only teams an administrator approved may use it.
            overseas=True,
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
            "default": item.id == model_roles.model("agent_model"),
            "supply": GATEWAY,
            # The tier an administrator set on the gateway (`cheese_tier`):
            # which plans may use it.
            "tier": item.tier,
            # The thinking efforts it honours (`cheese_efforts`).
            "efforts": list(item.efforts),
            # Only teams an administrator approved may use it (`cheese_overseas`).
            "overseas": item.overseas,
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
                "efforts": [],
                "overseas": False,
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


def catalog(project_settings: dict | None) -> dict[str, dict]:
    """这个项目能用的模型，按 id 排好 —— `model_choices` 的按 id 版。

    单独一步而不是藏在 `resolve` 里，是因为它按项目算一次就够，而读它的地方是按活
    循环的：一个房间的看板一屏几百张卡，每张卡重建一次全目录，构造的次数和卡数一样
    多，答案却完全一样。

    和 `model_choices` 住在一起，是因为答得出「这个项目能用哪些模型」的只有本包：
    目录的每一行都由这里的名单、池和骨架算出来（见上面那几段），按 id 排一次是同一
    件事的收尾。绑定一条活的那个包在第 4 层，够不着第 5 层，它拿在手里的就是这份排
    好的目录。

    每一行还带着 ``wire``：真发到上游、也真记进 `usage` 的那个名字。目录 id 和上游
    名不是一套词 —— 订阅模型的 id 是 ``sonnet`` 这样的短名，上游收的是
    ``claude-sonnet-5``；网关模型的 id 就是它自己那个名字。别名表在 `agent.market`，
    也是第 5 层，而两个要用这一道翻译的地方（`binding.resolve` 挑出来的绑定要发出去，
    `binding.catalog_id` 要把用量行反查回 id）都在第 4 层 —— 翻译随目录一起递下去，
    它们就不必各自去够上一层，也不再各自问一次「这是不是订阅」。
    """
    return {
        choice["id"]: dict(
            choice,
            wire=(
                subscription_model_alias(choice["id"])
                if choice["supply"] == SUBSCRIPTION
                else choice["id"]
            ),
        )
        for choice in model_choices(project_settings)
    }


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


class Scene(StrEnum):
    """Where a model is about to run, which decides whose choice applies.

    ``conversation`` is a channel's main line, a 支线 or a private chat;
    ``task`` is a task's own conversation; ``subagent`` is a subagent any of
    them spawns."""

    conversation = "conversation"
    task = "task"
    subagent = "subagent"


#: The platform role each scene runs on when nobody chose a model (「自动」).
SCENE_ROLES: dict[Scene, str] = {
    Scene.conversation: "agent_model",
    Scene.task: "task_model",
    Scene.subagent: "subagent_model",
}

#: The teammate's own choice for a scene, read before its model
#: (``AgentConfiguration.task_model`` / ``subagent_model``).
_TEAMMATE_SCENE_KEYS: dict[Scene, str] = {
    Scene.task: "task_model",
    Scene.subagent: "subagent_model",
}


def scene_of(task_id: object | None) -> Scene:
    """A teammate's scene in a conversation: a task when it is a task's own
    (``Place.task_id``), else a conversation."""
    return Scene.task if task_id is not None else Scene.conversation


@dataclass(frozen=True, slots=True)
class Pick:
    """The model a scene runs on, and whether the platform chose it.

    ``automatic`` is true when nobody chose: neither the teammate nor the
    project named a model, so the scene's platform role did. The team's plan
    does not limit such a model (``gate.Call.automatic``): the plan limits
    what a person may pick, and nobody picked this one."""

    binding: WorkBinding
    automatic: bool

    @property
    def model(self) -> str:
        return self.binding.model


def _named(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def chosen_model(
    scene: Scene, *, project_settings: dict | None, teammate: dict | None = None
) -> str | None:
    """The model somebody chose for ``scene``, in the order ``pick`` reads
    them; None when nobody did (「自动」)."""
    own = teammate or {}
    scene_key = _TEAMMATE_SCENE_KEYS.get(scene)
    for chosen in (
        _named(own.get(scene_key)) if scene_key else None,
        _named(own.get("model")),
        _named((project_settings or {}).get("default_model")),
    ):
        if chosen is not None:
            return chosen
    return None


def pick(
    scene: Scene,
    choices: dict[str, dict],
    *,
    project_settings: dict | None,
    teammate: dict | None = None,
) -> Pick:
    """The one answer to "which model does this run on".

    The teammate's choice for this scene (in a task, or for a subagent), then
    the teammate's model, then the project main model; only when none of them
    names one does the scene's platform role (``SCENE_ROLES``) run it. A job
    with no teammate (``teammate`` None) starts at the project main model.

    A chosen model the catalogue cannot serve is refused by name
    (``binding.resolve``, I27), never swapped for another. A role model this
    project's harness cannot drive gives way to the catalogue's default:
    nobody chose it, so nothing anyone chose is swapped.
    """
    chosen = chosen_model(scene, project_settings=project_settings, teammate=teammate)
    if chosen is not None:
        return Pick(
            binding.resolve(None, choices, default_model=chosen), automatic=False
        )
    role = model_roles.get(SCENE_ROLES[scene])
    if role is not None and role in choices:
        return Pick(binding.resolve(None, choices, default_model=role), automatic=True)
    return Pick(binding.resolve(None, choices), automatic=True)


def shown_model(
    scene: Scene,
    choices: dict[str, dict],
    *,
    project_settings: dict | None,
    teammate: dict | None = None,
) -> str | None:
    """The model a screen names for ``scene``: ``pick``'s, or the chosen name
    as written when the catalogue cannot serve it — refusing is the run's
    business, not the screen's."""
    try:
        return pick(
            scene, choices, project_settings=project_settings, teammate=teammate
        ).model
    except ValidationError:
        return chosen_model(scene, project_settings=project_settings, teammate=teammate)


def gate_call(picked: Pick, choices: dict[str, dict], *, approver: str) -> gate.Call:
    """``picked`` as the call the tier gate judges (``policy.gate.check``)."""
    choice = choices[picked.model]
    return gate.Call(
        resource=gate.Resource.model,
        subject=picked.model,
        label=choice.get("label", picked.model),
        tier=choice.get("tier", TIER_INCLUDED),
        approver=approver,
        overseas=bool(choice.get("overseas")),
        automatic=picked.automatic,
    )


def automatic_models() -> dict[str, str | None]:
    """What 「自动」 runs on now, scene by scene: the three roles' models."""
    return {scene.value: model_roles.get(role) for scene, role in SCENE_ROLES.items()}
