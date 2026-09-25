"""Resolve a work binding, teammate override, or project default against one catalog."""

from dataclasses import dataclass

from app.core.errors import ValidationError
from app.domain.agent.market import subscription_model_alias
from app.domain.agent.supply import SUBSCRIPTION
from app.domain.agent_instance.configuration import model_choices
from app.domain.room_task.models import Task


@dataclass(frozen=True, slots=True)
class WorkBinding:
    """一条活占用的模型资源。

    ``supply`` 跟着一起出，因为它不是从 id 上猜出来的 —— 订阅模型的 id 是
    ``sonnet`` 这样的短名，前缀里看不出它走订阅；目录（``catalog``）是唯一知道
    这件事的地方，而调用方都要它：轮次组装拿它决定要不要换成 Claude 的全名，
    准入拿它决定送哪个池。
    """

    model: str
    supply: str
    effort: str | None = None

    @property
    def wire_model(self) -> str:
        """真发到上游、也真记进 `usage` 的那个名字。

        目录 id 和上游名不是一套词：订阅模型的 id 是 `sonnet` 这样的短名，上游
        收的是 `claude-sonnet-5`；网关模型的 id 就是它自己那个名字。`catalog_id`
        是这一道翻译的反向，它们俩挨着放是因为知道两边互为表里的只有目录 ——
        把翻译摊到调用方，就是每个读绑定的地方各写一次「这是不是订阅」。
        """
        return (
            subscription_model_alias(self.model)
            if self.supply == SUBSCRIPTION
            else self.model
        )


def catalog(project_settings: dict | None) -> dict[str, dict]:
    """这个项目能用的模型，按 id 排好。

    单独一步而不是藏在 `resolve` 里，是因为它按项目算一次就够，而读它的地方是按
    活循环的：一个房间的看板一屏几百张卡，每张卡重建一次全目录，构造的次数和卡数
    一样多，答案却完全一样。
    """
    return {choice["id"]: choice for choice in model_choices(project_settings)}


def resolve(
    task: Task | None,
    choices: dict[str, dict],
    *,
    agent_model: str | None = None,
    default_model: str | None = None,
) -> WorkBinding:
    """Explicit work, then teammate, then supplied default, then project main model."""
    bound = (task.model or "").strip() if task is not None else ""
    bound = bound or agent_model or default_model or ""
    effort = task.effort if task is not None else None
    if not bound:
        default = next(
            (choice for choice in choices.values() if choice["default"]), None
        )
        if default is None:
            raise ValidationError("当前项目没有可用的默认模型，请检查模型服务")
        return WorkBinding(model=default["id"], supply=default["supply"], effort=effort)
    chosen = choices.get(bound)
    if chosen is None:
        raise ValidationError(
            f"这个任务绑定的模型 {bound!r} 在当前项目中不可用，需要重新选择"
        )
    return WorkBinding(model=chosen["id"], supply=chosen["supply"], effort=effort)


def catalog_id(spent: str, choices: dict[str, dict]) -> str | None:
    """真发出去的那个模型名 → 目录里的 id；目录认不出来就 `None`。

    目录和用量行说的不是同一套词：目录里订阅模型的 id 是 `sonnet` 这样的短名，
    真发到上游、也真记进 `usage` 的是 `claude-sonnet-5`。卡上一分钱没花时显示
    「用哪个模型」要从绑定算（短名），花过之后要从用量算（全名），两头直接吐出来
    就是同一张卡、同一个模型，在第一次请求之后换了个名字 —— 用户读到的是「模型
    被换了」。

    这一道翻译放在目录这边，不在渲染那边：知道 id 和全名互为表里的只有目录。
    网关模型的 id 就是它自己那个名字，所以只有订阅那一支要问别名。

    认不出来就 `None`，由调用方照原样显示：上游给回一个目录里没有的名字（换了
    代次、带上了日期），显示它真花在谁身上，仍然比显示一个猜出来的短名诚实。
    """
    if spent in choices:
        return spent
    for choice in choices.values():
        if choice["supply"] != SUBSCRIPTION:
            continue
        if subscription_model_alias(choice["id"]) == spent:
            return choice["id"]
    return None
