"""这条活用哪个模型 —— 绑在活上，不绑在参与者上（结论 3、44）。

模型曾经是 agent 的一个属性：想换模型就再建一个 agent。那是把「资源」当成了
「参与者」——同一个人换了把锤子，不会变成另一个人。分参与者的判据是 role，
模型和机器一样，是**一件工作占用的资源**，所以它落在活上：卡是用户接触模型的
唯一地方，卡上并排写着在哪台机器、用哪个模型。

## 次序只有两级，而且第一级可以没有

    这条活自己的绑定  →  项目默认

房间主线没有第一级 —— 它不是一条活，`resolve(None, …)` 拿到的永远是项目默认。
这是特意的（结论 3 第一句）：主线程一轮一换模型，等于每一轮都把 prompt 缓存
打掉，而主线程正是那条最长、最吃缓存的对话。

## 答不出就拒绝，不换池

一条活绑了本项目用不了的模型，这里**报错**，不悄悄退回项目默认（不变量 I27）。
静默换池正是 #325 G2 要杀掉的那种失败：屏幕上写着 Claude，跑的是别的东西，而
谁也不知道什么时候换的。

拒绝是给**执行**用的答案。渲染一张卡不是执行：看板要能把一条绑坏的活照原样显示
出来，否则唯一能看见、进而改掉这条绑定的那一屏，自己先打不开
（`presentation.card_model`）。

## 今天谁按活读它

只有卡片渲染。轮次组装（`agent/chat.py`）和准入（`api/routes/llm_proxy.py`）问的
都是房间主线，传的是 `None`。按活解析模型的那一刻是平台派子 agent 的那一刻，而
平台今天根本没有派活的路径 —— 它在 P33（骨架的子 agent 四条硬性要求）里出生，
那条 PR 依赖本条。

## 它只答「用哪个模型」

档位策略（超档变成给人的提议）不在这里，在 P38。这里答完就完。
"""

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


def resolve(task: Task | None, choices: dict[str, dict]) -> WorkBinding:
    """这一轮用哪个模型。``task=None`` 是房间主线，它永远走项目默认。"""
    bound = (task.model or "").strip() if task is not None else ""
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
        raise ValidationError(f"这条活绑的模型 {bound!r} 在当前项目里用不了，请改绑")
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
